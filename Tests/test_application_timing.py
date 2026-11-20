import json
import pathlib
import sys
import unittest

sys.path.insert(0,str(pathlib.Path(__file__).parents[1]/'tools'))
from application_timing import analyze, markers, summarize_clock


def marker_log(offset):
    rows=[]
    for i in range(1,7):
        row=dict(id=i,source_frame=(i-1)*48000,host_ns=i*1000000000+offset,
                 mapping='queue_sample_host_anchor',discontinuity=0,
                 translate_status=0,translated_flags=3,device_status=0,device_flags=3)
        rows.append('MACBEE_MARKER '+json.dumps(row))
    return '\n'.join(rows)


class TimingTests(unittest.TestCase):
    def test_retains_raw_four_timestamp_exchanges(self):
        rows=[dict(t1=i*1000,t2=i*1000+1100,t3=i*1000+1110,t4=i*1000+210)
              for i in range(20)]
        result=summarize_clock(rows)
        self.assertEqual(result['raw'],rows)
        self.assertEqual(result['offset_ns'],1000)
        self.assertEqual((result['lower_ns'],result['upper_ns']),(900,1100))

    def test_inconsistent_calibration_fails(self):
        rows=[dict(t1=i*1000,t2=i*1000+1100,t3=i*1000+1110,t4=i*1000+210)
              for i in range(20)]
        rows[-1]['t2']+=1000;rows[-1]['t3']+=1000
        with self.assertRaisesRegex(ValueError,'no constant-offset intersection'):summarize_clock(rows)

    def test_same_marker_device_mapping_with_clock_uncertainty(self):
        before={};after={}
        for host,offset,uncertainty in [('local',0,100000),('remote',10**12,500000)]:
            row=dict(offset_ns=offset,lower_ns=offset-uncertainty,upper_ns=offset+uncertainty,
                     asymmetry_scale_ms=uncertainty/1e6)
            before[host]=dict(row,reference_ns=0);after[host]=dict(row,reference_ns=10**10)
        result=analyze([marker_log(0),marker_log(10**12+2000000)],before,after)
        self.assertAlmostEqual(result['median_ms'],2)
        lo,hi=result['conditional_median_interval_ms']
        self.assertAlmostEqual(lo,1.4,places=3);self.assertAlmostEqual(hi,2.6,places=3)
        self.assertAlmostEqual(result['half_minimum_rtt_scale_ms'],.6)

    def test_requires_all_markers_and_valid_device_mapping(self):
        text=marker_log(0)
        with self.assertRaisesRegex(ValueError,'Six unique'):markers('\n'.join(text.splitlines()[:-1]))
        with self.assertRaisesRegex(ValueError,'Duplicate'):markers(text+'\n'+text.splitlines()[0])
        with self.assertRaisesRegex(ValueError,'valid queue'):markers(text.replace('"discontinuity": 0','"discontinuity": 1'))

    def test_timing_mode_is_explicit_and_defaults_off(self):
        source=(pathlib.Path(__file__).parents[1]/'tools/run_application_relay.py').read_text()
        self.assertIn("os.environ.get('MACBEE_OWNED_MARKER_TIMING')=='1'",source)
        self.assertIn('else:session=ApplicationTransport(manual_session=args.manual_session)',source)
        self.assertIn("if args.manual_session:parser.error('Manual sessions cannot use the finite marker diagnostic')",source)
        page=(pathlib.Path(__file__).parent/'fixtures/browser-owned-timing.html').read_text()
        self.assertIn('prefix=[-120,77,-33,110,-85,44,-17,(i+1)*13]',page)
        self.assertIn('spacing=1',page)
        self.assertIn('new OfflineAudioContext',page)
        self.assertNotIn('getUserMedia',page)


if __name__=='__main__':unittest.main()
