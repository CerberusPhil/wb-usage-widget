import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from popup_controller import PopupController, anchored_position


class ManualTimer:
    created = []
    def __init__(self, delay, callback):
        self.delay, self.callback = delay, callback
        self.cancelled = False
        self.created.append(self)
    def start(self):
        pass
    def cancel(self):
        self.cancelled = True


class PopupTests(unittest.TestCase):
    def setUp(self):
        ManualTimer.created = []
        self.patch = patch('popup_controller.threading.Timer', ManualTimer)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.native = {'visible': False, 'position': 'detached'}
        self.hovered = False
        self.queue = []
        self.popup = PopupController(
            lambda: self.native.update(visible=True),
            lambda: self.native.update(visible=False),
            lambda: self.native.update(position='anchor'), self.queue.append,
            hovered=lambda: self.hovered)
        self.addCleanup(self.popup.close)
    def drain(self):
        while self.queue:
            self.queue.pop(0)()
    def show(self):
        self.popup.show()
        self.drain()

    def test_transient_show_reanchors_and_hides_after_idle(self):
        self.show()
        self.assertEqual(self.native, {'visible': True, 'position': 'anchor'})
        timer = ManualTimer.created[-1]
        self.assertEqual(timer.delay, 5)
        timer.callback()
        self.drain()
        self.assertFalse(self.native['visible'])
        self.assertFalse(self.popup.state()['visible'])

    def test_stationary_pointer_holds_new_popup_without_dom_enter_event(self):
        self.hovered = True
        self.show()
        ManualTimer.created[-1].callback()
        self.drain()
        self.assertTrue(self.native['visible'])
        self.hovered = False
        ManualTimer.created[-1].callback()
        self.drain()
        self.assertFalse(self.native['visible'])

    def test_pin_prevents_queued_timeout_and_preserves_detached_position(self):
        self.show()
        ManualTimer.created[-1].callback()
        self.popup.set_pinned(True)
        self.drain()
        self.assertTrue(self.native['visible'])
        self.native['position'] = 'dragged'
        self.popup.hide()
        self.drain()
        self.show()
        self.assertEqual(self.native['position'], 'dragged')
        self.popup.set_pinned(False)
        self.drain()
        self.assertEqual(self.native['position'], 'anchor')
        ManualTimer.created[-1].callback()
        self.drain()
        self.assertFalse(self.native['visible'])

    def test_interacting_holds_timer_until_both_pointer_and_input_leave(self):
        self.show()
        timer = ManualTimer.created[-1]
        self.popup.activity('pointer', True)
        self.popup.activity('input', True)
        timer.callback()
        self.drain()
        self.assertTrue(self.native['visible'])
        self.popup.activity('pointer', False)
        self.assertEqual(len(ManualTimer.created), 1)
        self.popup.activity('input', False)
        self.assertEqual(len(ManualTimer.created), 2)
        ManualTimer.created[-1].callback()
        self.drain()
        self.assertFalse(self.native['visible'])

    def test_old_timeout_cannot_hide_new_show_or_closed_controller(self):
        self.show()
        old = ManualTimer.created[-1]
        self.popup.hide()
        self.drain()
        self.show()
        old.callback()
        self.drain()
        self.assertTrue(self.native['visible'])
        self.popup.close()
        ManualTimer.created[-1].callback()
        self.drain()
        self.assertTrue(self.native['visible'])

    def test_each_taskbar_edge_and_negative_monitor_coordinates(self):
        cases = [
            ((1700, 0, 1720, 25), (0, 30, 1920, 1080), (1500, 33)),
            ((1880, 1050, 1900, 1080), (0, 0, 1920, 1040), (1500, 590)),
            ((0, 900, 40, 940), (40, 0, 1920, 1080), (48, 630)),
            ((1880, 900, 1920, 940), (0, 0, 1880, 1080), (1452, 630)),
            ((-300, -1200, -280, -1175), (-1920, -1170, 0, -200), (-500, -1167)),
            ((10, 1050, 30, 1080), (0, 0, 1920, 1040), (0, 590)),
        ]
        for anchor, work, expected in cases:
            with self.subTest(anchor=anchor):
                self.assertEqual(anchored_position(anchor, work, (420, 450)), expected)


if __name__ == '__main__':
    unittest.main()
