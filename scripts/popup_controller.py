"""Tray popover lifetime and placement, independent of Cocoa/Win32."""
import threading


def anchored_position(anchor, work, size, gap=8):
    """Top-left coordinates in one coordinate space; keep the panel in work area."""
    left, top, right, bottom = anchor
    wl, wt, wr, wb = work
    width, height = size
    cx, cy = (left + right) / 2, (top + bottom) / 2
    if cy <= wt:
        edge = 'top'
    elif cy >= wb:
        edge = 'bottom'
    elif cx <= wl:
        edge = 'left'
    elif cx >= wr:
        edge = 'right'
    else:
        edge = min(('top', 'bottom', 'left', 'right'), key=lambda e: {
            'top': abs(cy - wt), 'bottom': abs(cy - wb),
            'left': abs(cx - wl), 'right': abs(cx - wr),
        }[e])
    if edge == 'top':
        x, y = cx - width / 2, bottom + gap
    elif edge == 'bottom':
        x, y = cx - width / 2, top - height - gap
    elif edge == 'left':
        x, y = right + gap, cy - height / 2
    else:
        x, y = left - width - gap, cy - height / 2
    return (round(max(wl, min(x, wr - width))),
            round(max(wt, min(y, wb - height))))


class PopupController:
    """Serialize native changes; a stale timeout cannot dismiss a new popover."""
    def __init__(self, show, hide, place, dispatch, delay=5.0, hovered=None):
        self._show_native, self._hide_native = show, hide
        self._place, self._dispatch = place, dispatch
        self._delay = delay
        self._hovered = hovered
        self._lock = threading.RLock()
        self._visible = self._pinned = self._closed = False
        self._holds = set()
        self._timer = None
        self._generation = 0

    def state(self):
        with self._lock:
            return {'visible': self._visible, 'pinned': self._pinned}

    def _arm(self):
        self._generation += 1
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._visible and not (self._closed or self._pinned or self._holds):
            generation = self._generation
            self._timer = threading.Timer(self._delay,
                lambda: self._dispatch(lambda: self._expire(generation)))
            self._timer.daemon = True
            self._timer.start()

    def _expire(self, generation):
        with self._lock:
            if generation != self._generation or self._closed:
                return
            # A window can appear under a stationary pointer without a DOM enter event.
            if self._hovered is not None and self._hovered():
                self._arm()
                return
            self._hide()

    def _show(self):
        with self._lock:
            if self._closed:
                return
            if not self._pinned:
                self._place()
            self._show_native()
            self._visible = True
            self._arm()

    def show(self):
        self._dispatch(self._show)

    def _hide(self):
        with self._lock:
            if self._hide_native() is False:
                return
            self._visible = False
            self._holds.clear()
            self._arm()

    def hide(self):
        self._dispatch(self._hide)
        return True

    def toggle(self):
        def apply():
            with self._lock:
                if self._visible:
                    self._hide()
                else:
                    self._show()
        self._dispatch(apply)

    def set_pinned(self, value):
        with self._lock:
            self._pinned = bool(value)
            self._arm()
            if not self._pinned and self._visible:
                self._dispatch(self._place_if_unpinned)
            return self.state()

    def _place_if_unpinned(self):
        with self._lock:
            if self._visible and not self._pinned and not self._closed:
                self._place()

    def activity(self, kind='pulse', active=False):
        with self._lock:
            if kind in ('pointer', 'input'):
                if active and self._visible:
                    self._holds.add(kind)
                else:
                    self._holds.discard(kind)
            self._arm()
        return True

    def close(self):
        with self._lock:
            self._closed = True
            self._holds.clear()
            self._arm()
