import pathlib
import re
import unittest


PAGE = (pathlib.Path(__file__).parent / "fixtures/browser-owned-audio.html").read_text()


class BrowserFixtureContractTests(unittest.TestCase):
    """Offline source-contract checks; these never open a browser or audio device."""

    def test_bounded_owned_pattern(self):
        self.assertIn("rate=48000,delay=4,spacing=.5,duration=.1,count=6,peak=.005", PAGE)
        self.assertIn("tone.frequency.value=880", PAGE)
        self.assertIn("setTimeout(close,300000)", PAGE)
        self.assertIn("window.addEventListener('pagehide'", PAGE)
        self.assertIn("play.onclick=()=>{play.disabled=true", PAGE)

    def test_same_generator_is_qualified_before_play(self):
        self.assertIn("new OfflineAudioContext", PAGE)
        self.assertIn("markers(off,.1)", PAGE)
        self.assertIn("markers(context,start", PAGE)
        self.assertIn("prepare.disabled=!qualified", PAGE)
        self.assertIn("if(!qualified)throw Error", PAGE)
        self.assertIn("buffer=null;channel=null", PAGE)

    def test_timing_and_subquantization_are_explicit(self):
        self.assertIn("performance.now()", PAGE)
        self.assertIn("performance.timeOrigin+perf", PAGE)
        self.assertIn("context.getOutputTimestamp()", PAGE)
        self.assertIn("zero.offset.value=.000001", PAGE)
        self.assertLess(.000001 * 32768, .5)
        self.assertIn("stamp('marker_ended'", PAGE)

    def test_no_microphone_storage_or_remote_requests(self):
        self.assertIn("default-src 'none'", PAGE)
        self.assertIn("connect-src 'none'", PAGE)
        self.assertIsNone(re.search(
            r"getUserMedia|MediaRecorder|localStorage|sessionStorage|indexedDB|"
            r"fetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon|https?://", PAGE))


if __name__ == "__main__":
    unittest.main()
