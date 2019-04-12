# app.py
import datetime
import functools
import os
import re
import urllib

from flask import (Flask, abort, flash, Markup, redirect, render_template, 
					request, Response, session, url_for)
from markdown import markdown
from markdown.extensions.codehilite import CodeHiliteExtension
from markdown.extensions.extra import ExtraExtension
from micawber import bootstrap_basic, parse_html
from micawber.cache import Cache as OEmbedCache
from peewee import *
from playhouse.flask_utils import FlaskDB, get_object_or_404, object_list
from playhouse.sqlite_ext import *

import pws

ADMIN_PASSWORD = pws.admin_pw
APP_DIR = os.path.dirname(os.path.realpath(__file__))
DATABASE = "sqliteext:///%s" % os.path.join(APP_DIR, "blog.db")
DEBUG = False
SECRET_KEY = pws.secret_key # for flask-wtf. Pick a different secret key in each application that you build and make sure that this string is not known by anyone
SITE_WIDTH = 800


app = Flask(__name__)
app.config.from_object(__name__)

flask_db = FlaskDB(app)
database = flask_db.database

oembed_providers = bootstrap_basic(OEmbedCache())


# defining models:
# Flask-SQLAlchemy baseclass is "Model"
# SQLAlchemy = Python SQL toolkit and Object Relational Mapper that gives application developers the full power and flexibility of SQL.

class Entry(flask_db.Model): # each web form is represented by a class that inherits from class Form . The class defines the list of fields in the form, each represented by an object.
	title = CharField()
	slug = CharField(unique=True)
	content = TextField()
	published = BooleanField(index=True) # from flask-wtf, extension for working w web forms, safe against CSRF
	timestamp = DateTimeField(default=datetime.datetime.now, index=True)

	@property
	def html_content(self):
		hilite = CodeHiliteExtension(linenums=False, css_class='highlight')
		extras = ExtraExtension()
		markdown_content = markdown(self.content, extensions=[hilite, extras])
		oembed_content = parse_html(
			markdown_content,
			oembed_providers,
			urlize_all=True,
			maxwidth=app.config["SITE_WIDTH"])
		return Markup(oembed_content) # The Markup object tells Flask that we trust the HTML content, so it will not be escaped when rendered in the template.

	def save(self, *args, **kwargs): #ensure that when an entry is saved, we also generate a slug from the title
		if not self.slug:
			self.slug = re.sub('[^\w]+', '-', self.title.lower())
		ret = super(Entry, self).save(*args, **kwargs)

		#store search content
		self.update_search_index()
		return ret

	def update_search_index(self): #ensure that search index is updated
		search_content = "\n".join((self.title, self.content))
		try:
			fts_entry = FTSentry.get(FTSEntry.docid == self.id)
		except FTSEntry.DoesNotExist:
			FTSEntry.create(docid=self.id, content=search.content)
		else:
			fts_entry.content = search_content
			fts_entry.save()

	@classmethod
	def public(cls):
		return Entry.select().where(Entry.published == True)

	'''
	Because we're only displaying published entries on the index and search 
	results, we'll need a way for logged-in users to manage the list of 
	draft posts. Let's add a protected view for displaying draft posts. 
	We'll add another classmethod to Entry, and add new view below the 
	existing index() view.
	'''

	@classmethod
	def drafts(cls):
		return Entry.select().where(Entry.published == False)


	@classmethod
	def search(cls, query):
		words = [word.strip() for word in query.split() if word.strip()]
		if not words:
			# return empty query
			return Entry.select().where(Entry.id == 0)
		else:
			search = " ".join(words)

		return (Entry
			.select(Entry, FTSEntry.rank().alias("score"))
			.join(FTSEntry, on=(Entry.id == FTSEntry.docid))
			.where(
				(Entry.published == True) &
				(FTSEntry.match(search)))
			.order_by(SQL("score")))


class FTSEntry(FTSModel):
	content = SearchField()

	class Meta:
		database = database


# add authentification:

def login_required(fn):
	@functools.wraps(fn)
	def inner(*args, **kwargs):
		if session.get("logged_in"):
			return fn(*args, **kwargs)
		return redirect(url_for("login", next=request.path))
	return inner


@app.route("/login/", methods=["GET", "POST"])
def login():
	next_url = request.args.get("next") or request.form.get("next")
	if request.method == "POST" and request.form.get("password"):
		password = request.form.get("password")
		if password == app.config["ADMIN_PASSWORD"]:
			session["logged_in"] = True
			session.permanent = True # cookie to store session
			flash("You are now logged in.", "success")
			return redirect(next_url or url_for("index"))
		else:
			flash("Incorrect password.", "danger")
	return render_template("login.html", next_url=next_url)

@app.route("/logout/", methods=["GET", "POST"])
def logout():
	if request_method == "POST":
		session.clear()
		return redirect(url_for("login"))
	return render_template("logout.html")

'''When logging in, if you simply navigate to /login/ in your browser, 
you will see a rendered template with a password field. 
When you submit the form, though, the view will check the submitted 
password against the configured ADMIN_PASSWORD, and conditionally 
redirect or display an error message.
'''

# now implement views w flask_utils playhouse
# homepage index view: newest to oldest, 20 at a time
# The FlaskDB class is a wrapper for configuring and referencing a Peewee database 
# from within a Flask application. 

@app.route("/")
def index():
	search_query = request.args.get("q")
	if search_query:
		query = Entry.search(search_query) # This method will use the SQLite full-text search index to query for matching entries. SQLite's full-text search supports boolean queries, quoted phrases, and more.
	else:
		query = Entry.public().order_by(Entry.timestamp.desc()) # You may notice that we're also calling Entry.public() if no search is present. This method will return only published entries.
	return object_list("index.html", query, search=search_query)



@app.route("/create/", methods=["GET", "POST"])
@login_required
def create():
	if request.method == "POST":
		if request.form.get("title") and request.form.get("content"):
			entry = Entry.create(title=request.form["title"], content=request.form["content"], published=request.form.get("published") or False)
			flash("Entry created successfully.", "success")
			if entry.published:
				return redirect(url_for("detail", slug=entry.slug))
			else:
				return redirect(url_for("edit", slug=entry.slug))
		else:
			flash("Title and Concent are required.", "danger")
	return render_template("create.html")

'''
Add the following view below the index view. 
This view will use the login_required decorator to ensure only 
logged-in users can access it:
'''

@app.route("/drafts/")
@login_required
def drafts():
	query = Entry.drafts().order_by(Entry.timestamp.desc())
	return object_list("index.html", query)


'''
create slugs / urls
Our detail view will accept a single parameter, the slug, 
and then attempt to match that to an Entry in the database. 
The catch is that if the user is logged-in we will allow them to view 
drafts, but if the user is not, we will only show public entries.
'''

@app.route("/<slug>/")
def detail(slug):
	if session.get("logged_in"):
		query = Entry.select()
	else:
		query = Entry.public()
	entry = get_object_or_404(query, Entry.slug == slug) # The get_object_or_404 helper is defined in the playhouse flask_utils module and, if an object matching the query is not found, will return a 404 response.
	return render_template("detail.html", entry=entry)


'''
Now we need two new views for creating and editing entries. 
These views will have a lot in common, but for clarity we'll implement 
them as two separate view functions.

basically we're going to do different things depending on the 
request method. If the request method is GET, then we will display 
a form allowing the user to create or edit the given entry. 
If the method is POST we will assume they submitted the form on the 
page (which we'll get to when we cover templates), and after doing 
some simple validation, we'll either create a new entry or update the 
existing one. 
'''

'''edit view: similar to create (s.o.)'''
@app.route("/<slug>/edit/", methods=["GET", "POST"])
@login_required
def edit(slug):
	entry = get_object_or_404(Entry, Entry.slug == slug)
	if request.form.get("title") and request.form.get("content"):
		entry.title = request.form["title"]
		entry.content = request.form["content"]
		entry.published = request.form.get("published") or False
		entry.save()

		flash("Entry saved successfully.", "success")
		if entry.published:
			return redirect(url_for("detail", slug=entry.slug))
		else:
			return redirect(url_for("edit", slug=entry.slug))
	else:
		flash("Title and Content are required.", "danger")

	return render_template("edit.html", entry=entry)


# let's add app initialization code, a hook for handling 404s, and a template filter we'll use later on.
@app.template_filter('clean_querystring')
def clean_querystring(request_args, *keys_to_remove, **new_values):
	querystring = dict((key, value) for key, value in request_args.items())
	for key in keys_to_remove:
		querystring.pop(key, None)
	querystring.update(new_values)
	return urllib.urlencode(querystring)

@app.errorhandler(404)
def not_found(exc):
	return Response("<h3>Not FOUND</h3>"), 404



def main():
	database.create_tables([Entry, FTSEntry])
	app.run(debug=True)

if __name__ == "__main__":
	main()


# https://github.com/coleifer/peewee/tree/master/examples/blog/templates