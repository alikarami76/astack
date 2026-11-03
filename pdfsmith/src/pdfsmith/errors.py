class PdfsmithError(Exception):
    exit_code = 1


class InputError(PdfsmithError):
    """doc.yaml, content, template or theme problem the caller can fix."""

    exit_code = 1


class RenderError(PdfsmithError):
    """Chrome or Paged.js failed to produce a PDF."""

    exit_code = 2


class EnvError(PdfsmithError):
    """A required tool (Chrome, vendored files) is missing."""

    exit_code = 4
