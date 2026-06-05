from app.services.place_photos import photo_from_tags


def test_commons_file_tag_becomes_thumbnail_url():
    photo = photo_from_tags({"wikimedia_commons": "File:Kotor Old Town.JPG"})

    assert photo is not None
    assert photo.source == "Wikimedia Commons"
    assert photo.url == "https://commons.wikimedia.org/wiki/Special:FilePath/Kotor_Old_Town.JPG?width=960"
    assert photo.source_url == "https://commons.wikimedia.org/wiki/File:Kotor_Old_Town.JPG"


def test_direct_http_image_tag_is_allowed():
    photo = photo_from_tags({"image": "https://example.com/place.jpg"})

    assert photo is not None
    assert photo.url == "https://example.com/place.jpg"
    assert photo.source == "OpenStreetMap image"


def test_non_http_image_tag_is_ignored():
    assert photo_from_tags({"image": "javascript:alert(1)"}) is None
