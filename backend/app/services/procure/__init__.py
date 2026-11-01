"""Fetching books: the indexers, Prowlarr and the download programs the operator connects, and the
rules for what is fetched (formats, places, the automatic work).

``gate`` is the one way out to all of them. ``newznab`` and ``indexers`` speak to the indexers, ``prowlarr`` takes the
indexers of a Prowlarr, ``clients`` speaks to the download programs, ``formats`` and ``automation`` keep what the
operator chose.
"""
