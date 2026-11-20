"""Offline license and GPLv3 Section 5(a) modification-notice compliance tests."""

import hashlib
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import release_backend as rb

GPL3_VERBATIM_SHA256 = 'd62f065830aa3739cc031156b9690805c7b2e811b4a178c8b4acd8725d561c94'
ARCHIVE = os.environ.get('MACBEE_QOS_ARCHIVE')


class RootLicenseComplianceTests(unittest.TestCase):
    def test_root_license_is_verbatim_gpl3(self):
        license_path = ROOT / 'LICENSE'
        self.assertTrue(license_path.is_file(), 'Missing root LICENSE file')
        raw = license_path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), GPL3_VERBATIM_SHA256)
        self.assertEqual(len(raw), 35142)
        text = raw.decode('utf-8')
        self.assertIn('GNU GENERAL PUBLIC LICENSE', text.splitlines()[0])
        self.assertIn('Version 3, 29 June 2007', text.splitlines()[1])
        self.assertIn('Copyright (C) 2007 Free Software Foundation, Inc.', text)
        self.assertIn('why-not-lgpl.html', text)

    def test_root_third_party_notices_completeness(self):
        notices_path = ROOT / 'THIRD-PARTY-NOTICES.md'
        self.assertTrue(notices_path.is_file(), 'Missing root THIRD-PARTY-NOTICES.md')
        text = notices_path.read_text(encoding='utf-8')
        required = (
            'GPL-3.0-or-later',
            'Copyright (C) 2026 podggg',
            'https://github.com/podggg/macbee',
            'Snapcast 0.35.0',
            'Copyright (C) 2014-2025 Johannes Pohl and contributors',
            'https://github.com/snapcast/snapcast/tree/v0.35.0',
            'macbee-backend-corresponding-source.tar.gz',
            'Boost 1.86.0',
            'BSL-1.0',
            'https://www.boost.org/users/history/version_1_86_0.html',
            'aixlog 1.5.1',
            'Copyright (c) 2017-2021 Johannes Pohl; bundled header (C) 2017-2025 Johannes Pohl',
            'popl 1.3.1 (header label)',
            'Copyright (c) 2015-2016 Johannes Pohl; bundled header (C) 2015-2021 Johannes Pohl',
            'nlohmann/json 3.11.3',
            'Copyright (c) 2013-2022 Niels Lohmann; bundled header SPDX-FileCopyrightText: 2013-2023 Niels Lohmann',
            '/usr/bin/python3',
            '/usr/bin/ssh',
        )
        for item in required:
            self.assertIn(item, text)
        self.assertNotIn('by the private release', text)

    def test_release_backend_third_party_notices_template(self):
        text = rb.render_third_party_notices()
        for item in (
            'Macbee: Copyright (C) 2026 podggg.',
            'Source: https://github.com/podggg/macbee',
            'GPL-3.0-or-later',
            'Snapcast 0.35.0: Copyright (C) 2014-2025 Johannes Pohl and contributors.',
            'Boost 1.86.0: Boost Software License 1.0 (BSL-1.0.txt).',
            'Bundled aixlog, popl, and nlohmann/json: MIT; see MIT-components.txt.',
            'GPLv3 Section 5(a) file-level modification notices',
            'macbee-backend-corresponding-source.tar.gz',
            'No restriction on recipients\' GPL rights is added by any distribution.',
        ):
            self.assertIn(item, text)
        self.assertNotIn('by the private release', text)


class SnapcastModificationNoticeTests(unittest.TestCase):
    def test_every_patched_snapcast_file_gets_gpl_5a_notice_synthetic(self):
        specs = rb.patches()
        patched_paths = sorted({path for path, _, _ in specs})
        self.assertEqual(
            patched_paths,
            [
                'client/player/coreaudio_player.cpp',
                'client/player/coreaudio_player.hpp',
                'client/player/file_player.cpp',
                'client/player/file_player.hpp',
                'server/snapserver.cpp',
                'server/streamreader/asio_stream.hpp',
            ],
        )
        self.assertEqual(set(patched_paths), set(rb.MODIFICATION_NOTICES))

        notice_specs = [row for row in specs if row[1] == rb.GPL_HEADER_END]
        self.assertEqual([row[0] for row in notice_specs], patched_paths)

        upstream_stub = (
            '/***\n'
            '    This file is part of snapcast\n'
            '    Copyright (C) 2014-2025  Johannes Pohl\n\n'
            + rb.GPL_HEADER_END
            + '\n#include "example.hpp"\n'
        )
        for rel, old, new in notice_specs:
            self.assertEqual(upstream_stub.count(old), 1)
            patched = upstream_stub.replace(old, new)
            rb.verify_modification_notice(rel, patched)
            self.assertIn('Copyright (C) 2014-2025  Johannes Pohl', patched)
            self.assertIn(rb.MODIFICATION_NOTICE_DATE, patched)

        # Verify full-range transformation preserves and updates the §5(a) notice.
        core_notice = next(row for row in notice_specs if row[0] == 'client/player/coreaudio_player.cpp')
        synthetic_core = (
            upstream_stub.replace(core_notice[1], core_notice[2])
            + '\n// Diagnostic safety ceiling: only this agreed low-amplitude synthetic test.\n'
            + 'if(magnitude>164) {}\n'
        )
        full_core = rb.make_full_range_coreaudio(synthetic_core)
        rb.verify_modification_notice('full-range/coreaudio_player.cpp', full_core)
        self.assertIn('full-range S16 playback', full_core)
        self.assertIn('if(magnitude>32768)', full_core)
        self.assertNotIn('if(magnitude>164)', full_core)

    def test_modification_notice_guards_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, 'Missing GPLv3 Section 5\\(a\\)'):
            rb.modification_notice_block('server/untracked_file.cpp')

        upstream_only = '/***\n    This file is part of snapcast\n' + rb.GPL_HEADER_END + '\nint x = 0;\n'
        with self.assertRaisesRegex(RuntimeError, 'Missing prominent GPLv3 Section 5\\(a\\)'):
            rb.verify_modification_notice('server/snapserver.cpp', upstream_only)

        missing_header = (
            f'// Modified for Macbee (https://github.com/podggg/macbee), {rb.MODIFICATION_NOTICE_DATE}:\n'
            '// Licensed under GPL-3.0-or-later.\n'
        )
        with self.assertRaisesRegex(RuntimeError, 'Missing preserved upstream GPL header'):
            rb.verify_modification_notice('server/snapserver.cpp', missing_header)

        with self.assertRaisesRegex(RuntimeError, 'Unexpected full-range patch target'):
            rb.make_full_range_coreaudio(upstream_only)

    @unittest.skipUnless(ARCHIVE and Path(ARCHIVE).is_file(), 'requires MACBEE_QOS_ARCHIVE pointing to snapcast-v0.35.0.tar.gz')
    def test_real_snapcast_archive_patches_and_gpl_5a_notices(self):
        archive = Path(ARCHIVE)
        self.assertEqual(rb.digest(archive), rb.ARCHIVES['snapcast-v0.35.0.tar.gz'])
        specs = rb.patches()
        patched_paths = sorted({path for path, _, _ in specs})
        with tempfile.TemporaryDirectory(prefix='macbee-gpl5a-verify-') as scratch:
            source = Path(scratch) / 'source'
            rb.extract(archive, source, 'snapcast-0.35.0')
            self.assertEqual((source / 'LICENSE').read_bytes(), (ROOT / 'LICENSE').read_bytes())
            for rel, old, new in specs:
                p = source / rel
                text = p.read_text()
                self.assertEqual(text.count(old), 1, (rel, old[:60]))
                p.write_text(text.replace(old, new))
            with tarfile.open(archive) as tar:
                for rel in patched_paths:
                    upstream_text = tar.extractfile('snapcast-0.35.0/' + rel).read().decode()
                    reconstructed = rb.apply_file_patches(rel, upstream_text, specs)
                    self.assertEqual((source / rel).read_text(), reconstructed)
                    rb.verify_modification_notice(rel, reconstructed)
            base_cpp = source / 'client/player/coreaudio_player.cpp'
            self.assertEqual(rb.digest(base_cpp), rb.BASE_SHA)
            full_cpp_text = rb.make_full_range_coreaudio(base_cpp.read_text())
            self.assertEqual(hashlib.sha256(full_cpp_text.encode()).hexdigest(), rb.FULL_SHA)


if __name__ == '__main__':
    unittest.main()
