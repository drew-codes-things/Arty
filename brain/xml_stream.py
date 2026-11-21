import zipfile
import zlib
from xml.parsers import expat

MAX_PART_BYTES = 60 * 1024 * 1024
CHUNK_BYTES = 1 << 16
ENTITY_MARKER = b"<!ENTITY"
UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


class FileReadError(Exception):
    pass


class StopReading(Exception):
    pass


def local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1]


def open_archive(path: str, what: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise FileReadError(f"that isn't a readable {what} file (it may be damaged, password protected or in an older "
                            "format; ask for it to be saved as a newer one)") from None


def part_names(archive: zipfile.ZipFile) -> set[str]:
    return set(archive.namelist())


def refuse_entities(chunk: bytes, tail: bytes, name: str):
    if ENTITY_MARKER in tail + chunk:
        raise FileReadError(f"{name} declares XML entities, which this reader refuses")


def feed_part(archive: zipfile.ZipFile, name: str, parser) -> None:
    try:
        info = archive.getinfo(name)
    except KeyError:
        raise FileReadError(f"the file has no {name} inside") from None
    if info.file_size > MAX_PART_BYTES:
        raise FileReadError(f"{name} is too big to read ({info.file_size // (1024 * 1024)} MB inside the file)")
    total, tail = 0, b""
    try:
        with archive.open(info) as stream:
            while chunk := stream.read(CHUNK_BYTES):
                if total == 0 and chunk.startswith(UTF16_BOMS):
                    raise FileReadError(f"{name} uses an encoding this reader refuses")
                total += len(chunk)
                if total > MAX_PART_BYTES:
                    raise FileReadError(f"{name} is too big to read")
                refuse_entities(chunk, tail, name)
                tail = chunk[-len(ENTITY_MARKER):]
                parser.Parse(chunk, False)
            parser.Parse(b"", True)
    except (zipfile.BadZipFile, OSError, expat.ExpatError, EOFError, zlib.error) as error:
        raise FileReadError(f"{name} is damaged ({type(error).__name__})") from None


def walk(archive: zipfile.ZipFile, name: str, on_start, on_end, on_text) -> bool:
    parser = expat.ParserCreate(namespace_separator="}")
    parser.buffer_text = True

    def started(tag, attributes):
        on_start(local_name(tag), {local_name(key): value for key, value in attributes.items()})

    def refuse_doctype(*_):
        raise FileReadError(f"{name} has a document type declaration, which this reader refuses")

    parser.StartElementHandler = started
    parser.EndElementHandler = lambda tag: on_end(local_name(tag))
    parser.CharacterDataHandler = on_text
    parser.StartDoctypeDeclHandler = refuse_doctype
    parser.EntityDeclHandler = refuse_doctype
    try:
        feed_part(archive, name, parser)
    except StopReading:
        return False
    return True
