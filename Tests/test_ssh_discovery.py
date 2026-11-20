"""No real SSH configuration, DNS, network, shell command, or authentication."""
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from ssh_discovery import endpoint, list_aliases
import receiver_setup


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.home = Path(d.name)
        self.root = self.home / '.ssh'
        self.root.mkdir()

    def put(self, name, value):
        p = self.root / name
        p.parent.mkdir(exist_ok=True, parents=True)
        p.write_text(value)

    def read(self, **opts):
        with patch('subprocess.Popen', side_effect=AssertionError('Do not run ssh -G')), \
             patch('socket.socket', side_effect=AssertionError('Do not connect')):
            return list_aliases(self.home, default_user='tester', **opts)

    def test_safe_preview_include_first_value_and_patterns(self):
        self.put('config', 'Include = "inc/*.conf"\nHost=Desk Travel !Travel *.wild\n  HostName=%h.local\n  Port 2200\nHost *\n User fallback # comment\n')
        self.put('inc/01.conf', 'Host desk Other\n User alice\nHost Other\n   Port 2222\n')
        self.put('inc/02.conf', 'Host other\n Port 1111\n')
        r = self.read(configured=['alice@DESK.LOCAL:2200'])
        rows = {item['alias']: item for item in r['aliases']}
        self.assertEqual(list(rows), ['desk','Other','other','Desk'])
        self.assertEqual((rows['desk']['host'], rows['desk']['user'], rows['desk']['port']), ('desk','alice',22))
        self.assertFalse(rows['desk']['configured']);self.assertTrue(rows['Desk']['configured'])
        self.assertEqual(rows['Desk']['user'],'fallback')
        self.assertEqual(rows['other']['port'],1111)
        self.assertEqual(rows['Other']['port'], 2222)

    def test_match_exec_and_its_includes_never_evaluated(self):
        marker = self.home / 'not-created'
        self.put('config', 'Match exec "touch '+str(marker)+'"\n Include secret\n User wrong\nHost allowed\n User correct\n')
        self.put('secret', 'Host hidden\n HostName secret.local\n')
        r = self.read()
        self.assertEqual([(x['alias'],x['user']) for x in r['aliases']], [('allowed','correct')])
        self.assertFalse(marker.exists())
        self.assertIn('Match rules are not evaluated in previews', r['warnings'])

    def test_conditional_include_cycles_and_fifos(self):
        self.put('config', 'Include inc/main\nHost only\n Include scoped\nHost other\n')
        self.put('inc/main', 'Include config\nInclude fifo\nHost one\n')
        self.put('scoped', 'Host only stranger\n Port 2345\n')
        os.mkfifo(self.root / 'fifo')
        rows = {x['alias']:x for x in self.read()['aliases']}
        self.assertEqual(set(rows), {'one','only','other'})
        self.assertEqual((rows['only']['port'],rows['other']['port']),(2345,22))

    def test_missing_or_unresolved_config_is_not_fabricated(self):
        self.assertEqual(self.read()['aliases'],[])
        self.put('config', 'Host target\n HostName %d.invalid\n')
        self.assertEqual(self.read()['aliases'][0]['preview'],'unresolved')

    def test_argument_and_port_boundaries(self):
        for v in ['-oProxyCommand=bad','user@-x','a@b@c','a:','a:0','a:65536','a:1 b','x;ls','name\nnext','[::1]:22 x','2001:db8::1','[fe80::1%en0]','user@host:\u0662\u0662','not..a.host']:
            with self.subTest(v=v), self.assertRaises(ValueError):
                endpoint(v)
        self.assertEqual(endpoint('Foo').ssh_argv(), ['--','Foo'])
        self.assertEqual(endpoint('user@[2001:0db8:0:0::1]:2222').ssh_argv(), ['-p','2222','--','user@2001:db8::1'])
        self.assertEqual(endpoint('user@HOST:00022').canonical,'user@HOST:22')

    def test_equivalent_aliases_share_one_configured_marker(self):
        self.put('config', 'Host alias-a alias-b\n HostName receiver.local.\n User dev\n Port 2200\n')
        self.assertTrue(all(r['configured'] for r in self.read(configured=['alias-b'])['aliases']))
        self.assertTrue(all(r['configured'] for r in self.read(configured=['DEV@receiver.local:2200'])['aliases']))

    def test_case_sensitive_alias_routing_and_private_registry_preserve_case(self):
        from receiver_registry import configured_receivers
        self.put('config','Host MacMini\n HostName 10.9.8.7\n User dev\n Port 2222\nHost macmini\n HostName elsewhere.local\n')
        rows={r['alias']:r for r in self.read(configured=['MacMini'])['aliases']}
        self.assertEqual((rows['MacMini']['host'],rows['MacMini']['user'],rows['MacMini']['port']),('10.9.8.7','dev',2222))
        self.assertTrue(rows['MacMini']['configured']);self.assertFalse(rows['macmini']['configured'])
        self.assertEqual(endpoint('MacMini').ssh_argv(),['--','MacMini'])
        self.assertEqual(configured_receivers(dict(ssh_host='MacMini',remote_root='/tmp/fixture'))[0].ssh_host,'MacMini')

    def test_bonjour_and_manual_literal_and_alias_deduplicate_before_any_rpc(self):
        self.put('config','Host MiniAlias\n HostName Receiver.Local.\n')
        for saved in ['MiniAlias','receiver.local','tester@RECEIVER.LOCAL.:22']:
            for entered in ['MiniAlias','receiver.local:22','dev@receiver.local.','tester@RECEIVER.LOCAL.:22']:
                with self.subTest(saved=saved,entered=entered),patch.object(receiver_setup,'rpc',side_effect=AssertionError('No SSH')),patch.object(receiver_setup,'local_assets',side_effect=AssertionError('No staging')),self.assertRaisesRegex(ValueError,'already configured'):
                    receiver_setup.add_receiver(entered,'Duplicate',dict(ssh_host=saved,remote_root='/fixture/old'),self.home,ssh_home=self.home)

    def test_external_include_and_symlink_skip_with_warning(self):
        with tempfile.TemporaryDirectory() as outside:
            external=Path(outside)/'fragment';external.write_text('Host disallowed\n')
            (self.root/'link').symlink_to(external)
            self.put('config',f'Include {external}\nInclude link\nHost Inside\n')
            rows=self.read()
            self.assertEqual([r['alias'] for r in rows['aliases']],['Inside'])
            self.assertIn('Includes outside your home are not previewed',rows['warnings'])

    def test_duplicate_canonical_receiver_does_not_connect(self):
        config=dict(ssh_host='existing',remote_root='/fixture/old')
        with patch.object(receiver_setup,'rpc', side_effect=AssertionError('No connection')), \
             self.assertRaisesRegex(ValueError,'already configured'):
            receiver_setup.add_receiver('EXISTING','Duplicate',config,self.home,ssh_home=self.home)

    def test_full_parser_fixture_matrix_relative_glob_wildcards_negation_match_comments_duplicates(self):
        self.put(
            'config',
            '# Full-line comment\n'
            'Include conf.d/*.conf\n'
            'Include ~/extra_ssh.conf\n'
            'Host=Alpha Beta Alpha *.wild ?one !Beta\n'
            '  HostName=10.0.0.10 # inline comment\n'
            '  User=firstuser # inline comment\n'
            '  Port=2201\n'
            'Host Alpha\n'
            '  HostName 10.0.0.99\n'
            '  User seconduser\n'
            '  Port 9999\n'
            'Match host gamma\n'
            '  User matchuser\n'
            '  Port 2299\n'
            'Host gamma Gamma\n'
            '  HostName 10.0.0.20\n'
            'Host *\n'
            '  User fallbackuser\n'
            '  Port 2222\n',
        )
        self.put('conf.d/01-nested.conf', 'Include rel_from_dot_ssh.conf\nHost from-glob\n  User globuser\n')
        self.put('rel_from_dot_ssh.conf', 'Host from-relative\n  HostName rel.local\n  Port 2205\n')
        (self.home / 'extra_ssh.conf').write_text('Host from-tilde\n  HostName 2001:db8::5\n  Port 2206\n', encoding='utf-8')
        result = self.read()
        rows = {row['alias']: row for row in result['aliases']}
        # Beta negated by !Beta; *.wild and ?one excluded; Alpha deduplicated; gamma and Gamma distinct.
        self.assertEqual(
            list(rows),
            ['from-relative', 'from-glob', 'from-tilde', 'Alpha', 'gamma', 'Gamma'],
        )
        self.assertEqual((rows['Alpha']['host'], rows['Alpha']['user'], rows['Alpha']['port']), ('10.0.0.10', 'firstuser', 2201))
        self.assertEqual((rows['from-relative']['host'], rows['from-relative']['user'], rows['from-relative']['port']), ('rel.local', 'fallbackuser', 2205))
        self.assertEqual((rows['from-glob']['host'], rows['from-glob']['user'], rows['from-glob']['port']), ('from-glob', 'globuser', 2222))
        self.assertEqual((rows['from-tilde']['host'], rows['from-tilde']['user'], rows['from-tilde']['port']), ('2001:db8::5', 'fallbackuser', 2206))
        self.assertEqual((rows['gamma']['user'], rows['gamma']['port']), ('fallbackuser', 2222))
        self.assertEqual((rows['Gamma']['user'], rows['Gamma']['port']), ('fallbackuser', 2222))
        self.assertIn('Match rules are not evaluated in previews', result['warnings'])

    def test_discover_cli_and_receiver_host_lookup(self):
        import json
        from receiver_registry import AUTHORIZATION
        self.put('config', 'Host LivingRoom\n  HostName living-room.local.\n  User alex\n  Port 22\nHost Studio\n  HostName 10.0.0.50\n  Port 2222\nHost Unres\n  HostName %d.bad\n')
        cfg = {
            'receivers': [
                {'id': 'r-0000000000000001', 'name': 'Living Room', 'ssh_host': 'LivingRoom', 'remote_root': '/fixture/r1', 'client_sha256': '0' * 64, 'authorization': AUTHORIZATION},
                {'id': 'r-0000000000000002', 'name': 'Unresolved', 'ssh_host': 'Unres', 'remote_root': '/fixture/r2', 'client_sha256': '0' * 64, 'authorization': AUTHORIZATION},
            ],
        }
        discovered = receiver_setup.discover_destinations(cfg, ssh_home=self.home, default_user='tester')
        by_alias = {row['alias']: row for row in discovered['aliases']}
        self.assertTrue(by_alias['LivingRoom']['configured'])
        self.assertFalse(by_alias['Studio']['configured'])
        self.assertTrue(by_alias['Unres']['configured'])
        self.assertIn({'kind': 'address', 'host': 'living-room.local', 'port': 22}, discovered['configured_keys'])
        self.assertIn({'kind': 'unresolved', 'canonical': 'Unres'}, discovered['configured_keys'])

        # CLI --discover and --receiver-host
        out = []
        with patch.object(receiver_setup, 'settings', cfg), \
             patch('pathlib.Path.home', return_value=self.home), \
             patch.object(sys, 'argv', ['receiver_setup.py', '--discover']), \
             patch('builtins.print', side_effect=out.append):
            self.assertEqual(receiver_setup.main(), 0)
        payload = json.loads(out[0])
        self.assertTrue(payload['ok'])
        self.assertIn('configured_keys', payload['result'])

        out2 = []
        with patch.object(receiver_setup, 'settings', cfg), \
             patch.object(sys, 'argv', ['receiver_setup.py', '--receiver-host', 'r-0000000000000001']), \
             patch('builtins.print', side_effect=out2.append):
            self.assertEqual(receiver_setup.main(), 0)
        info = json.loads(out2[0])
        self.assertTrue(info['ok'])
        self.assertEqual(info['result'], {'id': 'r-0000000000000001', 'name': 'Living Room', 'ssh_host': 'LivingRoom', 'ssh_argv': ['--', 'LivingRoom']})

        out3 = []
        with patch.object(receiver_setup, 'settings', cfg), \
             patch.object(sys, 'argv', ['receiver_setup.py', '--receiver-host', 'r-ffffffffffffffff']), \
             patch('builtins.print', side_effect=out3.append):
            self.assertEqual(receiver_setup.main(), 1)
        self.assertFalse(json.loads(out3[0])['ok'])
