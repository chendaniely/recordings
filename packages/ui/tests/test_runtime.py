from recordings.archive import Archive
from recordings_ui import runtime


def test_the_media_base_is_set_with_the_archive_and_cleared_with_it(tmp_path):
    runtime.configure(Archive(tmp_path))
    assert runtime.media_base() is None  # the server's own /media/{id} route
    runtime.configure(Archive(tmp_path), media_base="../media/")
    assert runtime.media_base() == "../media/"
    runtime.configure(None)
    assert runtime.media_base() is None
