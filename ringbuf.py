"""Fixed-capacity FIFO for readings that could not be delivered.

A router reboot or a Vercel blip should cost you nothing: readings accumulate
here and backfill on the next successful POST. Because each reading carries its
own NTP-derived timestamp, backfilled points land at the correct place in
history rather than bunching up at the recovery moment.

Capacity is bounded on purpose. RAM is finite and an outage is unbounded, so
the only question is which readings to lose. We drop the OLDEST, because during
a long outage the recent readings are the ones you actually want when the link
comes back.
"""


class RingBuffer:
    def __init__(self, capacity=240):
        # 240 x 15 s = one hour of outage covered, ~30 kB at this payload size.
        self._cap = max(1, int(capacity))
        self._items = []
        self.dropped = 0        # cumulative; surfaced so the dashboard can flag gaps

    def push(self, item):
        self._items.append(item)
        while len(self._items) > self._cap:
            self._items.pop(0)
            self.dropped += 1

    def peek(self, n):
        """Oldest n items, without removing them.

        Peek-then-commit rather than pop-then-send: if the POST fails we must
        not have already destroyed the data we were trying to save.
        """
        return self._items[:n]

    def commit(self, n):
        """Discard the oldest n items, after they are confirmed delivered."""
        del self._items[:n]

    def __len__(self):
        return len(self._items)
