"""The group chat — his first room with more than two people in it.

Three parts, each on its own side of a line:

* :mod:`~infrastructure.telegram.client` talks to the Bot API and nothing else.
* :mod:`~infrastructure.telegram.listener` turns what the room said into rows,
  and keeps the polling cursor so a restart does not replay the night.
* what he *does* with the room — answering, choosing not to, mentioning it at a
  waking — lives beside the other autonomy consumers, not here.
"""
