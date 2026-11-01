"""The catalogue of an author's works: every book of an author the sources know, in every language, put
together into works, sorted by kind and series, and set against what a person has.

* ``gather``: which source is asked for which language (``sources/*.author_entries``), in the background only;
* ``people``: who of the name a record is by (the numbers of the sources, the years of the life): another person's
  records go, and for a name two people share a work nothing proves is not sure;
* ``works``: the records grouped into works, the rule of two sources, titles, years and series per language;
* ``compute``: a large catalogue put together in a helper process (the server does not wait for it);
* ``kinds``: novel, stories, biography, non-fiction, poems, for children, a box, or unsure;
* ``store``: the author and the job that builds the catalogue (a thinner one while a source is away does not stand);
* ``info``: a photo and a short text for an author nobody of the libraries is (two works of the catalogue prove it);
* ``view``: the catalogue as one person sees it (what they have, by the books they may see).
"""
