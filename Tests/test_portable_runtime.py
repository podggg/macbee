import hashlib,json,os,pathlib,subprocess,sys,tempfile,unittest

ROOT=pathlib.Path(__file__).parents[1]


class PortableRuntimeTests(unittest.TestCase):
    def test_bundled_assets_and_private_scratch_do_not_use_checkout_runtime(self):
        with tempfile.TemporaryDirectory(prefix='macbee-portable-') as temp:
            root=pathlib.Path(temp);assets=root/'assets';config=root/'private';(assets/'bin').mkdir(parents=True);config.mkdir(mode=0o700)
            hashes={}
            for name in ('snapserver','snapclient'):
                payload=('inert '+name).encode();(assets/'bin'/name).write_bytes(payload);hashes[name]=hashlib.sha256(payload).hexdigest()
            (assets/'backend-hashes.json').write_text(json.dumps(hashes))
            env=os.environ.copy();env.update(MACBEE_RUNTIME_HOME=str(config),MACBEE_ASSET_ROOT=str(assets))
            code='import sys,json;sys.path.insert(0,'+repr(str(ROOT/'tools'))+');import relay_runtime as r;print(json.dumps([str(r.ROOT),str(r.BIN),r.guard(verify=True)]))'
            result=subprocess.run([sys.executable,'-B','-E','-s','-c',code],env=env,capture_output=True,text=True,check=True)
            output=json.loads(result.stdout)
            self.assertEqual(output[0],str(config/'relay-runtime'));self.assertEqual(output[1],str(assets/'bin'))
            self.assertEqual((config/'relay-runtime').stat().st_mode&0o777,0o700)

    def test_user_build_resolves_resources_and_uses_standard_interpreter(self):
        source=(ROOT/'Sources/ControlledCapture/ApplicationSession.m').read_text()
        self.assertIn('if(packaged){root=bundle.resourcePath;python=@"/usr/bin/python3";}',source)
        self.assertIn('@"Library/Application Support/Macbee"',source)
        self.assertIn('@"-B",@"-E",@"-s"',source)
        self.assertIn('removeObjectForKey:@"PYTHONPATH"',source)
        build=(ROOT/'tools/build_application_candidate.py').read_text()
        self.assertIn("info.pop('MacbeeDevelopmentRoot');info.pop('MacbeePythonExecutable')",build)

    def test_updates_are_explicit_browser_link_not_downloader(self):
        source=(ROOT/'Sources/MacbeeApp/main.m').read_text()
        action=source.split('- (void)checkForUpdates:',1)[1].split('- (void)showDiagnostics:',1)[0]
        self.assertIn('https://github.com/podggg/macbee/releases',action)
        self.assertNotIn('/latest',action)
        self.assertNotIn('NSURLSession',action)
        self.assertIn('About Macbee',source)
        sys_source=(ROOT/'Sources/MacbeeApp/SystemMain.m').read_text()
        self.assertIn('https://github.com/podggg/macbee/releases',sys_source)


if __name__=='__main__':unittest.main()
