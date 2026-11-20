import os,pathlib,plistlib,shutil,subprocess,tempfile,unittest
ROOT=pathlib.Path(__file__).parents[1]
FAKE_TRANSPORT='''import os,struct,time,statistics,json,pathlib
class SystemAudioTransport:
 def __init__(self,outputs):
  self.outputs=outputs;self.sinks=['CoreAudioPlayer -> '+x+' default output' for x in outputs]
  self.renderer_identity=None;self.cleanup_verified=False;self.values=set();self.loss_sent=False
  if os.environ.get('MACBEE_SYSTEM_CASE')=='qos':
   import ctypes,threading
   lib=ctypes.CDLL(None)
   # An explicit default thread in this test alone makes this paired measurement
   # independent of the reviewer's parent task. Nothing is inferred from NSTask.
   self.natural=lib.qos_class_self()
   assert lib.pthread_set_qos_class_self_np(0x15,0)==0
   self.baseline=lib.qos_class_self();self.owner=threading.get_ident();self.measured=False
 def start(self):
  self.mode=os.environ.get('MACBEE_SYSTEM_CASE');self.markers=[]
  if os.environ.get('MACBEE_SYSTEM_CASE')=='wrong_outputs':self.outputs=['unknown']
 def check(self):
  if self.mode in ('server_limit','sender_limit'):
   if not hasattr(self,'epoch'):self.epoch=time.monotonic()
   if time.monotonic()-self.epoch>1:raise RuntimeError('Repeated '+self.mode.split('_')[0]+' pacing interruptions')
  if os.environ.get('MACBEE_SYSTEM_CASE')=='native_pressure':
   if not hasattr(self,'epoch'):
    self.epoch=time.monotonic();self.waits=[(1.05,.300),(1.75,.400),(2.65,.500)]
   if self.waits and time.monotonic()-self.epoch>=self.waits[0][0]:
    _,dt=self.waits.pop(0);time.sleep(dt)
 bounded_pacing=True
 def drain_events(self):
  if os.environ.get('MACBEE_SYSTEM_CASE')=='output_loss_clean' and self.values and not self.loss_sent:
   self.loss_sent=True;return [dict(output=self.outputs[-1],state='offline')]
  return []
 def write(self,data):
  if self.mode=='qos' and not self.measured:
   import ctypes,threading
   current=ctypes.CDLL(None).qos_class_self()
   assert self.owner==threading.get_ident() and self.baseline==0x15 and current>=0x19 and current>self.baseline, (self.baseline,current)
   pathlib.Path(os.environ["MACBEE_FIXTURE_RECEIPT"]).write_text(json.dumps(dict(natural=self.natural,untreated=self.baseline,actual_write=current)))
   self.measured=True
  if self.mode=='main_stalls':
   # Stereo markers encode successive 10 ms of an independent 48 kHz source.
   import array
   values=array.array('h',data)[::2]
   self.markers.append((time.clock_gettime(time.CLOCK_MONOTONIC_RAW),values))
  assert len(data)==3840;self.values.add(struct.unpack_from('<h',data)[0])
 def close(self):
  if os.environ.get('MACBEE_SYSTEM_CASE','normal')=='normal':assert 0 in self.values and len(self.values)>=3
  if os.environ.get('MACBEE_SYSTEM_CASE')=='native_pressure':assert not self.waits and len(self.values)>=3
  if self.mode=='main_stalls':
   flat=[v for _,row in self.markers for v in row]
   # Activation may land halfway through the already-running silent renderer's
   # first quantum. Begin on a source frame 300 ms in, before any main stall.
   flat=flat[flat.index(30):]
   assert len(flat)>120000
   bad=next(((i,v,30+i//480) for i,v in enumerate(flat) if v!=30+i//480),None)
   assert bad is None,repr(('padded, repeated, or missing real frames',bad))
   ages=[(at-row[-1]*.01) for at,row in self.markers if row[0]>=30]
   delta=statistics.median(ages[-18:])-statistics.median(ages[4:18])
   assert abs(delta)<.02,delta
   pathlib.Path(os.environ['MACBEE_FIXTURE_RECEIPT']).write_text(json.dumps(dict(frames=len(flat),post_minus_pre_ms=round(delta*1000,2))))
  self.cleanup_verified=True
'''
@unittest.skipUnless(shutil.which('xcrun'),'Apple compiler required')
class SystemSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='macbee-system-pipe-');cls.root=pathlib.Path(cls.temp.name)
        cls.tools=cls.root/'tools';cls.tools.mkdir()
        for name in ('session_diagnostics.py','run_system_relay.py','reader_clock.py','system_audio_protocol.py','application_protocol.py'):
            shutil.copyfile(ROOT/'tools'/name,cls.tools/name)
        (cls.tools/'system_audio_transport.py').write_text(FAKE_TRANSPORT)
        (cls.tools/'configured_transport.py').write_text('from system_audio_transport import SystemAudioTransport as ConfiguredTransport\n')
        cls.binary=cls.root/'session-cli';info=cls.root/'fixture-info.plist'
        info.write_bytes(plistlib.dumps(dict(CFBundleExecutable='test',CFBundleIdentifier='macbee.fixture.system',
            MacbeeDevelopmentRoot=str(cls.root),MacbeePythonExecutable='/usr/bin/python3',MacbeeApplicationTestEnabled=False,
            MacbeeBoundedSystemPacing=True,MacbeeSystemAudioEnabled=True,MacbeeManualSessionEnabled=True,MacbeeConfiguredOutputsEnabled=True)))
        flags=['xcrun','clang','-fno-modules','-O2','-Wall','-Wextra','-Werror','-mmacosx-version-min=14.2']
        obj=cls.root/'pcm.o';subprocess.run(flags+['-c',str(ROOT/'Sources/AudioPipeline/application_pcm.c'),'-o',str(obj)],check=True,timeout=30)
        result=subprocess.run(flags+['-fobjc-arc','-framework','Cocoa','-framework','CoreAudio',str(ROOT/'Sources/ControlledCapture/ApplicationSession.m'),
            str(ROOT/'Sources/ControlledCapture/ApplicationSource.m'),str(ROOT/'Tests/system_session_pipe_test.m'),str(obj),'-Wl,-sectcreate,__TEXT,__info_plist,'+str(info),'-o',str(cls.binary)],capture_output=True,text=True,timeout=30)
        if result.returncode:raise AssertionError(result.stderr)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def case(self,name):
        env=os.environ.copy();env['MACBEE_SYSTEM_CASE']=name;receipt=self.root/(name+'.json');env['MACBEE_FIXTURE_RECEIPT']=str(receipt)
        r=subprocess.run([str(self.binary)],env=env,capture_output=True,text=True,timeout=8)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertIn('no live metadata, capture, output or network',r.stdout)
        if name in ('main_stalls','qos'):print(name,receipt.read_text(),flush=True)
    def test_native_first_stop_keeps_both_specific_recovery_budget_codes(self):
        for mode in ('server_limit','sender_limit'):
            with self.subTest(mode=mode):self.case(mode)
    def test_hung_main_owner_cannot_keep_renewing(self):self.case('owner_hung')
    def test_relay_qos_rises_on_its_actual_sending_thread(self):self.case('qos')
    def test_main_thread_block_does_not_move_the_pcm_grid(self):self.case('main_stalls')
    def test_native_stamped_pipe_with_three_sender_pauses(self):self.case('native_pressure')
    def test_four_configured_outputs_real_native_pipe(self):self.case('configured_four')
    def test_nonterminal_loss_then_user_stop_keeps_native_cleanup_confirmed(self):self.case('output_loss_clean')
    def test_silent_start_then_pcm_without_restart(self):self.case('normal')
    def test_stop_during_registration(self):self.case('cancel_prepare')
    def test_source_health_loss(self):self.case('health_loss')
    def test_unselected_destination_rejected_before_capture(self):self.case('wrong_outputs')
if __name__=='__main__':unittest.main()
