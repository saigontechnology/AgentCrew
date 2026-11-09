"""Fictional byte-router/safety verification; no existing private input is used."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import random
import stat
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from AgentCrew.modules.memory.benchmark import BenchmarkError
from scripts import extract_memory_calibration as operator
from scripts.migrate_chroma_memory_to_sqlite import MigrationError, _private_directory


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def rejects(callback, reason=None):
    try:
        callback()
    except (BenchmarkError, MigrationError, OSError) as error:
        require(reason is None or str(error) == reason, "Wrong fictional rejection")
        return
    raise AssertionError("Invalid fictional extraction accepted")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def grammar_checks():
    positive = [
        (b'{"split":"calibration"}', "calibration"),
        (br'{"sp\u006cit":"calibr\u0061tion"}', "calibration"),
        (br'{"nested":{"split":"calibration"},"query":"\"split\":\"calibration\"","split":"holdout"}', "holdout"),
        (br'{"split":"holdout","values":[true,false,null,-0,0.5,1e+99,-2E-3,{},[],[[]]],"q":"\\\"\/\b\f\n\r\t"}', "holdout"),
        ('{"query":"Tiếng Việt 雪 😀","split":"calibration"}\r\n'.encode(), "calibration"),
        (br'{"q":"\ud83d\ude00","split":"holdout"}', "holdout"),
    ]
    for row, expected in positive:
        require(operator.row_split(row) == expected, "Fictional structural routing mismatch")
    bad = [
        b'', b'\n', b'[]', b'null', b'{}', b'{"nested":{"split":"calibration"}}',
        b'{"split":true}', b'{"split":null}', b'{"split":0}', b'{"split":{"split":"calibration"}}',
        b'{"split":"unknown"}', b'{"split":"Calibration"}', b'{"split":"holdout "}',
        b'{"split":"holdout","split":"holdout"}',
        br'{"split":"holdout","sp\u006cit":"calibration"}',
        br'{"split":"holdout","nested":{"a":1,"\u0061":2}}',
        '{"split":"holdout","😀":1,"\\ud83d\\ude00":2}'.encode(),
        b'{"split":"holdout",}', b'{,"split":"holdout"}', b'{"split":"holdout"}x',
        b'{"split":"holdout"}{"split":"calibration"}', b'{"split":"holdout"',
        b'{"q":[1,],"split":"holdout"}', b'{"q":[,1],"split":"holdout"}',
        b'{"q":01,"split":"holdout"}', b'{"q":-01,"split":"holdout"}',
        b'{"q":1.,"split":"holdout"}', b'{"q":.1,"split":"holdout"}',
        b'{"q":1e,"split":"holdout"}', b'{"q":NaN,"split":"holdout"}',
        b'{"q":Infinity,"split":"holdout"}', b'{"q":tru,"split":"holdout"}',
        br'{"q":"\x00","split":"holdout"}', br'{"q":"\u00q0","split":"holdout"}',
        br'{"q":"\ud800","split":"holdout"}', br'{"q":"\udc00","split":"holdout"}',
        br'{"q":"\ud800\u0020","split":"holdout"}',
        b'{"q":"\x00","split":"holdout"}', b'{"q":"\n","split":"holdout"}',
        b'{"q":"\xc0\xaf","split":"holdout"}', b'{"q":"\xed\xa0\x80","split":"holdout"}',
        b'{"q":"\xf4\x90\x80\x80","split":"holdout"}', b'{"q":"\xe2","split":"holdout"}',
        b'\xef\xbb\xbf{"split":"holdout"}',
    ]
    for row in bad:
        rejects(lambda row=row: operator.row_split(row))
    rng = random.Random(7301)
    rows = []
    for index in range(500):
        role = rng.choice(("calibration", "holdout"))
        payload = {"nested": [{"split": "not-the-discriminator", "escaped": '\\"split": "calibration"'},
                              rng.choice((None, True, False, index, -0.125, "雪 😀"))], "split": role}
        row = json.dumps(payload, ensure_ascii=bool(index % 2), indent=None).encode()
        rows.append((row, role))
    with ThreadPoolExecutor(max_workers=8) as pool:
        require(list(pool.map(operator.row_split, [row for row, _ in rows])) == [role for _, role in rows],
                "Concurrent router state leaked")
    with patch.object(operator, 'MAX_DEPTH', 3):
        rejects(lambda: operator.row_split(b'{"split":"holdout","v":[[[[]]]]}'), "extract_json_depth")
    with patch.object(operator, 'MAX_ROW_BYTES', 3):
        rejects(lambda: operator.row_split(positive[0][0]), "extract_row_size")
    return len(positive), len(bad), len(rows)


def extraction_checks(folder):
    source = folder / 'fictional-combined.jsonl'
    first = b' { "query" : "fictional calibration", "split":"calibration" }\r\n'
    discarded = br'{"split":"holdout","query":"fictional sentinel","labels":{"private_fake":true}}' + b'\n'
    last = br'{"nested":{"split":"holdout"},"sp\u006cit":"calibration","query":"last"}'
    raw = first + discarded + last
    source.write_bytes(raw)
    identity = digest(raw)
    output = folder / 'success'
    with patch('json.loads', side_effect=AssertionError('Payload object deserialization used')), \
            patch('json.load', side_effect=AssertionError('Payload object deserialization used')):
        receipt = operator.extract_calibration(source, output, identity)
    require((output / 'calibration-cases.jsonl').read_bytes() == first + last, "Original row bytes/newlines/order changed")
    require(source.read_bytes() == raw, "Fictional source changed")
    require(receipt['rows'] == {'calibration': 2, 'holdout': 1}
            and receipt['source_sha256'] == identity and receipt['export_sha256'] == digest(first + last),
            "Extraction identity/count mismatch")
    require(receipt['source_full_read_passes'] == 2 and receipt['reviewed_locally'] is False
            and receipt['valid_split_review'] is False, "Mechanical routing represented as human review")
    require(stat.S_IMODE(output.stat().st_mode) == 0o700
            and all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir()),
            "Private output permissions wrong")
    require(json.loads((output / 'extraction-evidence.json').read_text()) == receipt, "Evidence serialization mismatch")
    rejects(lambda: operator.extract_calibration(source, output, identity), 'extract_unsafe_output')
    require((output / 'calibration-cases.jsonl').read_bytes() == first + last, "Existing output overwritten")

    def clean_failure(content, expected=None, reason=None):
        source.write_bytes(content)
        target = folder / 'rejected'
        rejects(lambda: operator.extract_calibration(source, target, expected or digest(content)), reason)
        require(not target.exists() and not list(folder.glob('.calibration-extract-*')), "Partial extraction published or staging leaked")

    with patch.object(operator, 'row_split', side_effect=AssertionError('Routing before expected identity verified')):
        clean_failure(raw, '0' * 64, 'extract_source_identity_mismatch')
    clean_failure(raw, 'invalid', 'extract_expected_digest_required')
    clean_failure(first + discarded + b'{"split":"unknown"}\n', reason='extract_unknown_split')
    clean_failure(first + discarded + b'{"split":"calibration",}\n')
    clean_failure(discarded, reason='extract_both_splits_required')
    clean_failure(first, reason='extract_both_splits_required')
    clean_failure(b'', reason='extract_source_size')
    with patch.object(operator, 'MAX_SOURCE_BYTES', 5):
        clean_failure(raw, reason='extract_source_size')
    with patch.object(operator, 'MAX_ROW_BYTES', 5):
        clean_failure(raw, reason='extract_row_size')
    with patch.object(operator, 'MAX_ROWS', 1):
        clean_failure(raw, reason='extract_row_count')
    with patch.object(operator, '_publish', side_effect=BenchmarkError('fictional_publish_failure')):
        clean_failure(raw, reason='fictional_publish_failure')
    source.write_bytes(raw)
    real_publish = operator._publish
    target = folder / 'publish-race'
    def racing_publish(stage, final):
        final.mkdir()
        (final / 'sentinel').write_text('fictional existing output')
        real_publish(stage, final)
    with patch.object(operator, '_publish', racing_publish):
        rejects(lambda: operator.extract_calibration(source, target, identity), 'extract_atomic_publish_failed')
    require((target / 'sentinel').read_text() == 'fictional existing output'
            and not (target / 'calibration-cases.jsonl').exists(), "Atomic no-replace failed")
    real_router = operator.row_split
    def mutate(row):
        source.write_bytes(raw.replace(b'sentinel', b'changed!'))
        return real_router(row)
    with patch.object(operator, 'row_split', mutate):
        clean_failure(raw)
    source.write_bytes(raw)
    real_verify = operator._verified_source
    def between_passes(stream, expected):
        state = real_verify(stream, expected)
        source.write_bytes(raw.replace(b'sentinel', b'changed!'))
        return state
    with patch.object(operator, '_verified_source', between_passes):
        clean_failure(raw, reason='extract_source_changed')
    source.write_bytes(raw)
    def replace_path(row):
        temporary = folder / 'replacement'
        temporary.write_bytes(raw)
        os.replace(temporary, source)
        return real_router(row)
    with patch.object(operator, 'row_split', replace_path):
        clean_failure(raw)
    source.write_bytes(raw)
    link = folder / 'source-link'
    link.symlink_to(source)
    rejects(lambda: operator.extract_calibration(link, folder / 'link-rejected', identity))
    parent_link = folder / 'parent-link'
    parent_link.symlink_to(folder, target_is_directory=True)
    rejects(lambda: operator.extract_calibration(parent_link / source.name, folder / 'parent-rejected', identity))
    rejects(lambda: operator.extract_calibration(source, parent_link / 'output-rejected', identity))
    fifo = folder / 'fifo'
    os.mkfifo(fifo)
    rejects(lambda: operator.extract_calibration(fifo, folder / 'fifo-rejected', identity), 'extract_source_not_regular')
    require(not list(folder.glob('.calibration-extract-*')), 'Task staging leaked')


def main():
    counts = grammar_checks()
    root = ROOT / 'verification/memory/local'
    _private_directory(root)
    with TemporaryDirectory(prefix='fictional-extractor-', dir=root) as directory:
        extraction_checks(Path(directory))
    require('chromadb' not in sys.modules and 'voyageai' not in sys.modules, 'Provider SDK imported')
    print(json.dumps({'status': 'passed', 'fictional_only': True, 'valid_grammar_examples': counts[0],
                      'rejected_grammar_examples': counts[1], 'concurrent_generated_examples': counts[2],
                      'private_inputs_accessed': 0, 'real_exports_created': 0, 'benchmark_runs': 0,
                      'embeddings_models_network': 0, 'genuine_human_review_claimed': False,
                      'private_method_execution_authorized': False}))


if __name__ == '__main__':
    main()
