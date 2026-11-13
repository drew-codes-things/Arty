from typing import NamedTuple

DEVDOCS_CATALOGUE = "https://devdocs.io/docs.json"
DEVDOCS_DOCUMENTS = "https://documents.devdocs.io/{slug}/{name}"
DEVDOCS_PAGE = "https://devdocs.io/{slug}/{path}"
PYTHON_ARCHIVE = "https://docs.python.org/{version}/archives/python-{version}-docs-html.tar.bz2"
PYTHON_PAGE = "https://docs.python.org/{version}/{path}"
PEPS_ARCHIVE = "https://codeload.github.com/python/peps/tar.gz/refs/heads/main"
PEP_PAGE = "https://peps.python.org/pep-{number:04d}/"
DEBIAN_REFERENCE_PAGE = "https://www.debian.org/doc/manuals/debian-reference/{name}"
PROXMOX_GUIDE = "https://pve.proxmox.com/pve-docs/pve-admin-guide.html"
PSF_LICENCE = "PSF License (Python Software Foundation)"
PEPS_LICENCE = "Public domain or CC0-1.0-Universal, as stated in each PEP"
PROXMOX_LICENCE = "GNU Free Documentation License 1.3 (Proxmox Server Solutions GmbH)"
DEVDOCS_MISSING_LICENCES = {
    "http": "(c) 2005-2025 MDN contributors. Licensed under the Creative Commons Attribution-ShareAlike License v2.5 or later.",
    "man": "Each page keeps its own licence from the Linux man-pages project or the package it documents (see man7.org).",
}


class Source(NamedTuple):
    key: str
    family: str
    version: str
    kind: str
    location: str
    name: str


SOURCES = (
    Source("python-3.14", "python", "3.14", "python", "3.14", "Python 3.14"),
    Source("python-3.13", "python", "3.13", "python", "3.13", "Python 3.13"),
    Source("peps", "python", "", "peps", PEPS_ARCHIVE, "Python PEPs"),
    Source("javascript", "javascript", "", "devdocs", "javascript", "JavaScript (MDN)"),
    Source("dom", "javascript", "", "devdocs", "dom", "Web APIs (MDN)"),
    Source("html", "html", "", "devdocs", "html", "HTML (MDN)"),
    Source("css", "css", "", "devdocs", "css", "CSS (MDN)"),
    Source("http", "http", "", "devdocs", "http", "HTTP (MDN)"),
    Source("typescript", "typescript", "", "devdocs", "typescript", "TypeScript"),
    Source("node", "node", "26", "devdocs", "node", "Node.js 26"),
    Source("node-24", "node", "24", "devdocs", "node~24_lts", "Node.js 24 LTS"),
    Source("bash", "bash", "", "devdocs", "bash", "Bash"),
    Source("c", "c", "", "devdocs", "c", "C (cppreference)"),
    Source("cpp", "cpp", "", "devdocs", "cpp", "C++ (cppreference)"),
    Source("rust", "rust", "", "devdocs", "rust", "Rust"),
    Source("go", "go", "", "devdocs", "go", "Go"),
    Source("java-21", "java", "21", "devdocs", "openjdk~21", "Java SE 21 (OpenJDK)"),
    Source("kotlin", "kotlin", "", "devdocs", "kotlin~2", "Kotlin"),
    Source("php", "php", "", "devdocs", "php", "PHP"),
    Source("ruby-4.0", "ruby", "4.0", "devdocs", "ruby~4.0", "Ruby 4.0"),
    Source("ruby-3.4", "ruby", "3.4", "devdocs", "ruby~3.4", "Ruby 3.4"),
    Source("sqlite", "sqlite", "", "devdocs", "sqlite", "SQLite"),
    Source("postgresql-18", "postgresql", "18", "devdocs", "postgresql~18", "PostgreSQL 18"),
    Source("docker", "docker", "", "devdocs", "docker", "Docker"),
    Source("git", "git", "", "devdocs", "git", "Git"),
    Source("man", "linux", "", "devdocs", "man", "Linux man pages"),
    Source("proxmox", "proxmox", "", "page", PROXMOX_GUIDE, "Proxmox VE admin guide"),
    Source("debian", "debian", "", "deb", "debian-reference-en", "Debian Reference"),
)


def by_key(keys: list[str] | None) -> list[Source]:
    if not keys:
        return list(SOURCES)
    known = {source.key: source for source in SOURCES}
    unknown = [key for key in keys if key not in known]
    if unknown:
        raise ValueError(f"unknown sources {unknown}; known: {sorted(known)}")
    return [known[key] for key in keys]
