import sys
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_browser_requests import SHIPPED_CONFIG, lookups_run_for  # noqa: E402

import connections  # noqa: E402
import docs_reading  # noqa: E402
import native_calls  # noqa: E402
import research  # noqa: E402

DJANGO_QUESTION = "How do I run a background task with the tasks framework built into Django 6? Show the code."
KUBEFROST_QUESTION = "What flags does the kubefrost CLI take for snapshotting a Kubernetes namespace?"
OPENAI_QUESTION = ("My Python script fails at client = OpenAI() with TypeError: Client.__init__() got an unexpected keyword "
                   "argument 'proxies'. How do I fix it?")

DJANGO_RESULTS = """1. Django Tasks: Exploring the Built-in Tasks Framework - Real Python
   https://realpython.com/django-tasks/
   Explore Django Tasks, the new built-in framework in Django 6.0, and run background jobs with @task.
2. Django's Tasks framework | Django documentation | Django
   https://docs.djangoproject.com/en/6.1/topics/tasks/
   Django's Tasks framework. New in Django 6.0. Background Tasks can offload work to be run outside the request."""

DJANGO_PAGE = """Contents of https://docs.djangoproject.com/en/6.1/topics/tasks/:
New in Django 6.0:

For older Django versions, the [django-tasks](https://pypi.org/project/django-tasks/) backport is available.

Django's Tasks framework makes it easy to define and enqueue background work. It does not provide a worker.

## Configuring a Task backend[¶](#configuring-a-task-backend "Link to this heading")

The [`ImmediateBackend`](about:blank/ref/tasks/#django.tasks.backends.immediate.ImmediateBackend "ImmediateBackend") runs
enqueued Tasks immediately, rather than in the background:

```
TASKS = {"default": {"BACKEND": "django.tasks.backends.immediate.ImmediateBackend"}}
```

## Defining Tasks[¶](#defining-tasks "Link to this heading")

Tasks are defined using the [`django.tasks.task()`](about:blank/ref/tasks/#django.tasks.task "django.tasks.task") decorator on a
module-level function:

```
from django.core.mail import send_mail
from django.tasks import task


@task
def email_users(emails, subject, message):
    return send_mail(subject=subject, message=message, from_email=None, recipient_list=emails)
```

## Enqueueing Tasks[¶](#enqueueing-tasks "Link to this heading")

To add the Task to the queue store, so it will be executed, call the `enqueue()` method on it:

```
result = email_users.enqueue(emails=["user@example.com"], subject="You have a message", message="Hello there!")
```

## Unrelated history

""" + "The project began a long time ago and went through many releases with many people. " * 80

SNAPSHOTTER_RESULTS = """1. GitHub - kubernetes-csi/external-snapshotter: Sidecar container that watches snapshots
   https://github.com/kubernetes-csi/external-snapshotter
   If your Kubernetes distribution does not bundle the snapshot controller, install it. Snapshots deploy into the default namespace."""

SNAPSHOTTER_PAGE = ("Contents of https://github.com/kubernetes-csi/external-snapshotter:\n# CSI Snapshotter\n\n"
                    + "The snapshot controller watches VolumeSnapshot objects in a Kubernetes namespace and takes snapshots.\n\n"
                    + "```\n--leader-election --kubeconfig=/path --snapshot-name-prefix=snapshot\n```\n\n" * 6)

FORUM_RESULTS = """1. Client = OpenAI() returns error "Client.__init__() got an unexpected keyword argument 'proxies'"
   https://community.openai.com/t/client-openai-returns-error-proxies/1035249
   I updated to openai==1.55.3 and it works now. The proxies argument was removed."""

FORUM_PAGE = ("Contents of https://community.openai.com/t/client-openai-returns-error-proxies/1035249:\n"
              "My code:\n\n```\nfrom openai import OpenAI\nclient = OpenAI()\n```\n\n"
              "It returns error **TypeError: Client.**init**() got an unexpected keyword argument 'proxies'**\n\n"
              "The problem comes from incompatibility with the latest httpx==0.28.0 version and previous versions of openai. "
              "Openai v1.55.3 fixes the incompatibility issue, but you can also downgrade your httpx version to fix the error.\n\n"
              "Pinning openai==1.55.3 and httpx==0.27.2 did it for me.\n\n" + "Thanks, this helped a lot with my openai client.\n\n" * 10)


def fake_hub(results, pages, fetched=None, delay=0.0):
    def call(name, arguments, limit=connections.TOOL_RESULT_CHARS):
        if name == docs_reading.FETCH_TOOL:
            if fetched is not None:
                fetched.append(arguments["url"])
            time.sleep(delay)
            return pages.get(arguments["url"], "tool error 404: not found")[:limit]
        return results
    return call


def lookup_with(results, pages, fetched=None, question=DJANGO_QUESTION, delay=0.0):
    with unittest.mock.patch.object(connections.hub, "call", fake_hub(results, pages, fetched, delay)):
        return native_calls.look_up_first(SHIPPED_CONFIG, question)


class CodingLookupsReadTheDocs(unittest.TestCase):
    def test_the_official_docs_page_is_read_over_a_blog_listed_first(self):
        fetched = []
        used, notes, _ = lookup_with(DJANGO_RESULTS, {"https://docs.djangoproject.com/en/6.1/topics/tasks/": DJANGO_PAGE}, fetched)
        self.assertEqual(fetched, ["https://docs.djangoproject.com/en/6.1/topics/tasks/"])
        self.assertEqual(used, ["research__web_search", "fetch__fetch"])
        self.assertIn("from django.tasks import task", notes)
        self.assertIn("email_users.enqueue(", notes)
        self.assertIn("https://docs.djangoproject.com/en/6.1/topics/tasks/", notes)

    def test_the_model_is_told_to_follow_the_docs_over_its_memory(self):
        _, notes, _ = lookup_with(DJANGO_RESULTS, {"https://docs.djangoproject.com/en/6.1/topics/tasks/": DJANGO_PAGE})
        self.assertIn("follow this page, not your memory", notes)
        self.assertIn("use only the classes, methods and functions it shows", notes)

    def test_without_a_docs_site_the_top_result_is_read(self):
        fetched = []
        used, notes, _ = lookup_with(FORUM_RESULTS, {"https://community.openai.com/t/client-openai-returns-error-proxies/1035249": FORUM_PAGE},
                                     fetched, OPENAI_QUESTION)
        self.assertEqual(fetched, ["https://community.openai.com/t/client-openai-returns-error-proxies/1035249"])
        self.assertIn("fetch__fetch", used)
        self.assertIn("httpx==0.27.2", notes)
        self.assertIn("not the official documentation", notes)
        self.assertIn("any setup or versions on it are someone else's, not the user's", notes)

    def test_a_name_neither_the_page_nor_the_results_mention_is_reported_unconfirmed(self):
        fetched = []
        used, notes, _ = lookup_with(SNAPSHOTTER_RESULTS, {"https://github.com/kubernetes-csi/external-snapshotter": SNAPSHOTTER_PAGE},
                                     fetched, KUBEFROST_QUESTION)
        self.assertEqual(fetched, ["https://github.com/kubernetes-csi/external-snapshotter"])
        self.assertEqual(used, ["research__web_search", "fetch__fetch"])
        self.assertNotIn("--leader-election", notes)
        self.assertIn("never mentions kubefrost", notes)
        self.assertIn("nothing found confirms that kubefrost exists", notes)

    def test_a_page_missing_a_name_the_results_do_mention_is_just_left_out(self):
        results = SNAPSHOTTER_RESULTS + "\n2. kubefrost: freeze a namespace\n   https://blog.example.com/kubefrost\n   kubefrost snapshots."
        used, notes, _ = lookup_with(results, {"https://github.com/kubernetes-csi/external-snapshotter": SNAPSHOTTER_PAGE},
                                     question=KUBEFROST_QUESTION)
        self.assertEqual(used, ["research__web_search"])
        self.assertNotIn("--leader-election", notes)
        self.assertNotIn("never mentions", notes)

    def test_a_slow_page_is_given_up_quickly_and_the_search_results_still_used(self):
        started = time.monotonic()
        with unittest.mock.patch.object(docs_reading, "FETCH_SECONDS", 0.2):
            used, notes, _ = lookup_with(DJANGO_RESULTS, {"https://docs.djangoproject.com/en/6.1/topics/tasks/": DJANGO_PAGE},
                                         delay=1.5)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(used, ["research__web_search"])
        self.assertIn("Django's Tasks framework", notes)

    def test_a_failed_fetch_leaves_just_the_search_results(self):
        used, notes, _ = lookup_with(DJANGO_RESULTS, {})
        self.assertEqual(used, ["research__web_search"])
        self.assertNotIn("couldn't be checked", notes)

    def test_other_lookups_never_fetch_a_page(self):
        self.assertNotIn(docs_reading.FETCH_TOOL, lookups_run_for("will it rain tomorrow?"))


class DocsExcerpt(unittest.TestCase):
    def test_links_become_their_text_and_heading_anchors_go(self):
        text = docs_reading.readable(DJANGO_PAGE)
        self.assertNotIn("](", text)
        self.assertNotIn("¶", text)
        self.assertNotIn("Contents of https://", text)
        self.assertIn("`django.tasks.task()` decorator", text)

    def test_the_excerpt_keeps_the_matching_code_whole_within_the_budget(self):
        excerpt = docs_reading.excerpt(docs_reading.readable(DJANGO_PAGE), DJANGO_QUESTION, 900)
        self.assertLessEqual(len(excerpt), 900)
        self.assertEqual(excerpt.count("```") % 2, 0)
        self.assertIn("@task\ndef email_users", excerpt)
        self.assertNotIn("went through many releases", excerpt)

    def test_names_in_the_question_must_be_on_the_page(self):
        self.assertEqual(docs_reading.named_things(KUBEFROST_QUESTION), {"kubefrost"})
        self.assertEqual(docs_reading.named_things("How do I use requests.get_json_safely() in Python?"), {"get_json_safely"})
        self.assertEqual(docs_reading.named_things(OPENAI_QUESTION), {"client", "proxies"})
        self.assertIn("django", docs_reading.named_things(DJANGO_QUESTION))
        self.assertTrue(docs_reading.names_all(docs_reading.readable(FORUM_PAGE), docs_reading.named_things(OPENAI_QUESTION)))

    def test_docs_sites_are_preferred(self):
        self.assertTrue(docs_reading.is_docs_site("https://docs.python.org/3/library/string.templatelib.html"))
        self.assertTrue(docs_reading.is_docs_site("https://pypi.org/project/django-tasks/"))
        self.assertTrue(docs_reading.is_docs_site("https://requests.readthedocs.io/en/latest/"))
        self.assertFalse(docs_reading.is_docs_site("https://realpython.com/python-t-strings/"))
        self.assertEqual(docs_reading.page_to_read(research.relevant_results(DJANGO_RESULTS, DJANGO_QUESTION), DJANGO_QUESTION),
                         "https://docs.djangoproject.com/en/6.1/topics/tasks/")


TSTRING_QUESTION = ("How do I use Python 3.14 template strings (t-strings) to build an HTML snippet with the user's name "
                    "escaped? Show the code.")
TSTRING_RESULTS = """1. Python 3.14: Template Strings (T-Strings) - Real Python
   https://realpython.com/python-t-strings/
   Python 3.14 introduces t-strings: a safer, more flexible alternative to f-strings.
2. string - Common string operations - Python 3.15.2 documentation
   https://docs.python.org/3/library/string.html
   Source code: Lib/string/__init__.py String constants: The constants defined in this module are: Custom string formatting.
3. string.templatelib - Support for template string literals
   https://docs.python.org/3/library/string.templatelib.html
   Template strings are a mechanism for custom string processing. They have the full flexibility of Python's f-strings,
   but return a Template instance. To write a t-string, use a 't' prefix."""


class WhichPageIsRead(unittest.TestCase):
    def test_the_docs_page_that_matches_the_question_best_is_read(self):
        self.assertEqual(docs_reading.page_to_read(TSTRING_RESULTS, TSTRING_QUESTION),
                         "https://docs.python.org/3/library/string.templatelib.html")

    def test_with_no_docs_site_the_top_result_is_read_even_if_a_later_one_repeats_the_question(self):
        results = ("1. Client() returns error proxies\n   https://community.example.com/t/1035249\n   Updated openai and it works.\n"
                   "2. How can I resolve TypeError Client init got an unexpected keyword argument proxies in OpenAI\n"
                   "   https://askai.example.app/search/proxies\n   The error message TypeError Client init unexpected keyword "
                   "argument proxies in the OpenAI Python library.")
        self.assertEqual(docs_reading.page_to_read(results, OPENAI_QUESTION), "https://community.example.com/t/1035249")


class LongToolResults(unittest.TestCase):
    def test_a_caller_can_ask_the_hub_for_more_than_the_usual_cap(self):
        hub = connections.ToolHub()
        hub.routes = {"fetch__fetch": "http://hub/fetch/fetch"}
        with unittest.mock.patch.object(connections, "http_json", lambda *args, **named: "x" * 20000):
            self.assertEqual(len(hub.call("fetch__fetch", {})), connections.TOOL_RESULT_CHARS)
            self.assertEqual(len(hub.call("fetch__fetch", {}, limit=15000)), 15000)


if __name__ == "__main__":
    unittest.main()
