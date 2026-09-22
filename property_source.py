"""Strict property feed readers; legal free text uses an explicit structural dialect."""
import csv
import io
import re
import zipfile
from decimal import Decimal

HEADERS = {
 'parcel': 'PARCEL_ID SECTION TOWNSHIP RANGE SUB PARCEL DORUS_CODE DORDESC DORDESC1 NH_CD NH_DSCR HOMESTEAD OTHEREX EXCODE EXDESC PORT_VAL CLS_LND_VAL AG_CLASS VALUETYPE VALUEDESC TOT_LND_VAL TOT_BLD_VAL TOT_XF_VAL TOTALVAL RECONCILE ASSESSVAL TAXVAL CURTAXDIST TAXDIST AMTDUE MILLRATE YR_CREATED YR_IMPROVED LAST_INSP_DT TOT_ACREAGE PR_STRAP'.split(),
 'owner': 'PARCEL_ID LN_NUM NAME PCTOWN MAILTO ADDR_1 ADDR_2 ADDR_3 CITY STATE ZIP'.split(),
 'legal': 'PARCEL_ID NUM SECTION TOWNSHIP RANGE SUB PARCEL DSCR'.split(),
}
LEGAL_START = re.compile(r'^' + ','.join([r'"([0-9]+)"'] * 7) + ',')


def legal_rows(stream):
    """Retain complete physical source record; never silently discard orphan lines.

    The accepted source has exactly one record per physical line. Seven quoted
    numeric fields identify each record; DSCR has unescaped inch quotes. Newline
    continuations are NOT inferred. Legacy description rendering is explicit and
    separate from lossless raw_record retained in the snapshot.
    """
    if next(csv.reader([stream.readline()], strict=True)) != HEADERS['legal']:
        raise ValueError('Legal header drift')
    for line_number, line in enumerate(stream, 2):
        if '\x00' in line:
            raise ValueError(f'NUL in legal source at line {line_number}')
        match = LEGAL_START.match(line)
        if not match:
            raise ValueError(f'Ambiguous legal record boundary at line {line_number}')
        yield finish_legal(list(match.groups()), [line[match.end():]], [line])


def finish_legal(structural, descriptions, raw_lines):
    parts = []
    for text in descriptions:
        text = text.rstrip('\r\n')
        if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        else:
            # Outer wrapper is incomplete: preserve source, but do not invent a repair.
            raise ValueError('Unbalanced legal description wrapper')
        parts.append(text)
    rendered = ' '.join(p for p in parts if p).strip()
    return structural + [rendered], ''.join(raw_lines)


def rows(feed, path):
    with zipfile.ZipFile(path) as z:
        members = [m for m in z.infolist() if not m.is_dir()]
        if len(members) != 1:
            raise ValueError('Expected one source member')
        encoding = 'cp1252' if feed == 'legal' else 'utf-8-sig'
        with z.open(members[0]) as raw, io.TextIOWrapper(raw, encoding=encoding, errors='strict', newline='') as stream:
            if feed == 'legal':
                yield from legal_rows(stream)
            else:
                reader = csv.reader(stream, strict=True)
                if next(reader) != HEADERS[feed]:
                    raise ValueError('Source header drift')
                for row in reader:
                    if len(row) != len(HEADERS[feed]) or any('\x00' in s for s in row):
                        raise ValueError('Invalid source row width or NUL')
                    yield row, None


def identity(feed, row):
    if len(row) != len(HEADERS[feed]) or not re.fullmatch('[0-9]{18}', row[0]):
        raise ValueError('Invalid source parcel key or width')
    line = 0
    if feed != 'parcel':
        if not re.fullmatch('[0-9]+', row[1]) or int(row[1]) > 2147483647:
            raise ValueError('Invalid source line key')
        line = int(row[1])
    if feed == 'owner' and row[3]:
        if not re.fullmatch(r'(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)', row[3]) or Decimal(row[3]) > 100:
            raise ValueError('Invalid ownership percentage')
    return row[0], line
