import json,pathlib,shutil,struct,subprocess,sys,tempfile,unittest
ROOT=pathlib.Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from capture_protocol import PacketDecoder,null_stats
from run_capture_relay import consume_packets,fixture_packets

class CaptureProtocolTests(unittest.TestCase):
    def test_fixture_fragmentation_and_content(self):
        decoder=PacketDecoder();stream=b''.join(fixture_packets())
        payloads=[]
        for i in range(0,len(stream),1297):payloads+=decoder.feed(stream[i:i+1297])
        result=decoder.finish()
        self.assertEqual(result,dict(frames=312000,pulses=6,nonzero_frames=27132,peak=163))
        self.assertEqual(sum(map(len,payloads)),312000*4)
    def test_bad_headers_and_samples(self):
        cases=[b'xxxx'+struct.pack('<I',1),b'MBP1'+struct.pack('<I',4097),
               b'MBP1'+struct.pack('<Ihh',1,169,169),b'MBP1'+struct.pack('<Ihh',1,10,-10)]
        for packet in cases:
            with self.subTest(packet=packet),self.assertRaises(ValueError):PacketDecoder().feed(packet)
    def test_truncated_and_zero_only_not_success(self):
        for stream in (b'',b'MBP1\x01\0\0\0\0',b'MBP1\0\0\0\0'):
            decoder=PacketDecoder();decoder.feed(stream)
            with self.assertRaises(ValueError):decoder.finish()
    def test_after_end_and_length_bound(self):
        decoder=PacketDecoder();decoder.feed(b'MBP1'+bytes(4))
        with self.assertRaises(ValueError):decoder.feed(b'x')
        with self.assertRaises(ValueError):PacketDecoder().feed(bytes(65537))
        decoder=PacketDecoder();decoder.frames=480000
        with self.assertRaises(ValueError):decoder.feed(b'MBP1'+struct.pack('<Ihh',1,0,0))
    def test_consumer_eof_timeout_and_pipe_failure(self):
        with self.assertRaises(ValueError):consume_packets(lambda:b'',lambda x:None)
        clock=iter([0,13])
        with self.assertRaises(TimeoutError):consume_packets(lambda:None,lambda x:None,lambda:next(clock))
        def broken(data):raise BrokenPipeError('fixture pipe closed')
        with self.assertRaises(BrokenPipeError):consume_packets(lambda:next(fixture_packets()),broken)
    def test_counter_evidence_is_nonzero_not_padding(self):
        row=dict(requests=200,good_chunks=198,pattern_frames=0,zero_frames=200000,other_frames=27132)
        self.assertEqual(null_stats('MACBEE_STATS '+json.dumps(row)),row)
        for key,value in [('other_frames',0),('other_frames',40000),('good_chunks',0),('pattern_frames',1)]:
            bad=dict(row);bad[key]=value
            with self.assertRaises(ValueError):null_stats('MACBEE_STATS '+json.dumps(bad))
    @unittest.skipUnless(shutil.which('clang'),'existing clang required')
    def test_native_converter_cross_language(self):
        with tempfile.TemporaryDirectory(prefix='macbee-encoder-') as temp:
            binary=pathlib.Path(temp)/'encoder'
            subprocess.run(['clang','-std=c11','-fno-modules','-O2','-Wall','-Wextra','-Werror','-I'+str(ROOT/'Sources/AudioPipeline'),
                str(ROOT/'Sources/AudioPipeline/controlled_pcm.c'),str(ROOT/'Tests/controlled_pcm_test.c'),'-lm','-o',str(binary)],check=True,capture_output=True,timeout=30)
            subprocess.run([str(binary)],check=True,capture_output=True,timeout=5)
            data=subprocess.run([str(binary),'fixture'],check=True,capture_output=True,timeout=5).stdout
            decoder=PacketDecoder()
            for i in range(0,len(data),723):decoder.feed(data[i:i+723])
            self.assertEqual(decoder.finish(),dict(frames=312000,pulses=6,nonzero_frames=27132,peak=163))

if __name__=='__main__':unittest.main()
