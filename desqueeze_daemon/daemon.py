from __future__ import annotations

import dataclasses
import json
import re
import subprocess
import typing as t

import wand
import wand.color
import wand.image
from loguru import logger

if t.TYPE_CHECKING:
    import pathlib

POSSIBLE_ANAMORPHIC_FOCAL_LENGTHS = [0, 24, 50]
ANAMORPHIC_SCALE_FACTOR = 1.33
BLACK = wand.color.Color("black")
DEFAULT_FOCAL_LENGTH = "35.0A mm"


@dataclasses.dataclass
class Daemon:
    import_path: pathlib.Path
    export_path: pathlib.Path
    focal_length: str = DEFAULT_FOCAL_LENGTH

    def is_anamorphic(self, *, image_metadata: dict[str, t.Any]) -> bool:
        match = re.match(r"(\d+)\.\d+ mm", image_metadata["FocalLength"])
        if match is None:
            return False

        focal_length = int(match.group(1))

        return focal_length in POSSIBLE_ANAMORPHIC_FOCAL_LENGTHS and image_metadata["FNumber"] in ["undef", 0]

    def desqueeze_file(self, *, filepath: pathlib.Path, image_metadata: dict[str, t.Any]) -> None:
        dng_image_path = self.convert_to_dng(filepath=filepath)
        with wand.image.Image(filename=dng_image_path) as image:
            self.set_dng_anamorphic_ratio(image_path=dng_image_path, image_metadata=image_metadata)
            self.add_thumbnails(image=image, image_path=dng_image_path)

        filepath.unlink()

    def set_dng_anamorphic_ratio(self, *, image_path: pathlib.Path, image_metadata: dict[str, t.Any]) -> None:
        args = [
            "/usr/bin/exiftool",
            "-overwrite_original_in_place",
            f"-DefaultScale={ANAMORPHIC_SCALE_FACTOR} 1",
        ]
        if image_metadata["FocalLength"] == 0:
            args.append(f"-FocalLength={self.focal_length}")

        args.append(image_path.as_posix())
        subprocess.run(
            args,
            capture_output=True,
            check=True,
            shell=False,
        )

    def add_thumbnails(self, *, image: wand.image.Image, image_path: pathlib.Path) -> None:
        jpeg_image = self.generate_jpeg_from_raw(image=image)

        jpg_image_path = image_path.parent / (image_path.name + "_preview.jpg")
        self.generate_jpeg_thumbnail(image=jpeg_image, image_path=jpg_image_path, width=1024)
        self.set_and_delete_jpeg_thumbnail(
            image_path=image_path,
            thumbnail_path=jpg_image_path,
            thumbnail_id="PreviewImage",
        )

        jpeg_image.save(filename=jpg_image_path.as_posix())
        self.set_and_delete_jpeg_thumbnail(
            image_path=image_path, thumbnail_path=jpg_image_path, thumbnail_id="JpgFromRaw"
        )

    def generate_jpeg_from_raw(self, *, image: wand.image.Image) -> wand.image.Image:
        jpeg_image = image.clone()
        jpeg_image.format = "jpeg"
        jpeg_image.compression_quality = 95
        width, height = self.calculate_desqueezed_size(image=image)
        self.resize_srgb(image=jpeg_image, new_width=width, new_height=height)
        return jpeg_image

    def set_and_delete_jpeg_thumbnail(
        self,
        *,
        image_path: pathlib.Path,
        thumbnail_path: pathlib.Path,
        thumbnail_id: str,
    ) -> None:
        subprocess.run(
            [
                "/usr/bin/exiftool",
                "-overwrite_original_in_place",
                f"-{thumbnail_id}<={thumbnail_path.as_posix()}",
                image_path.as_posix(),
            ],
            capture_output=True,
            check=True,
            shell=False,
        )
        thumbnail_path.unlink()

    def generate_jpeg_thumbnail(
        self,
        *,
        image: wand.image.Image,
        image_path: pathlib.Path,
        width: int,
        height: int | None = None,
    ) -> None:
        thumbnail = image.clone()
        thumbnail.format = "jpeg"
        thumbnail.compression_quality = 95

        thumbnail_width, thumbnail_height = self.get_scaled_size(image=thumbnail, new_width=width)
        thumbnail.thumbnail(thumbnail_width, thumbnail_height)

        if height is not None and height > thumbnail_height:
            thumbnail_height_offset = round((height - thumbnail_height) / 2) * -1
            thumbnail.background_color = BLACK
            thumbnail.extent(width, height, 0, thumbnail_height_offset)

        thumbnail.save(filename=image_path.as_posix())

    def get_scaled_size(self, *, image: wand.image.Image, new_width: int) -> tuple[int, int]:
        return new_width, round(new_width * image.height / image.width)

    def resize_srgb(self, *, image: wand.image.Image, new_width: int, new_height: int) -> None:
        image.depth = 16
        image.transform_colorspace("rgb")
        image.resize(new_width, new_height, filter="lanczos2")
        image.transform_colorspace("srgb")
        image.depth = 8

    def calculate_desqueezed_size(self, *, image: wand.image.Image) -> tuple[int, int]:
        is_portrait = image.width < image.height

        if is_portrait:
            new_width = image.width
            new_height = round(image.height * ANAMORPHIC_SCALE_FACTOR)
        else:
            new_width = round(image.width * ANAMORPHIC_SCALE_FACTOR)
            new_height = image.height

        return new_width, new_height

    def convert_to_dng(self, *, filepath: pathlib.Path) -> pathlib.Path:
        dng_image_path = (self.export_path / filepath.name).with_suffix(".dng")
        subprocess.run(
            [
                "/dnglab/target/release/dnglab",
                "convert",
                "-f",
                "--crop",
                "none",
                filepath.as_posix(),
                dng_image_path.as_posix(),
            ],
            capture_output=True,
            check=True,
            shell=False,
        )
        return dng_image_path

    def get_metadata(self, *, filepath: pathlib.Path) -> dict[str, t.Any]:
        result = subprocess.run(
            ["/usr/bin/exiftool", "-j", filepath.as_posix()],
            capture_output=True,
            check=True,
            shell=False,
        )
        return json.loads(result.stdout)[0]

    def desqueeze(self) -> None:
        file_paths = [e for e in self.import_path.iterdir() if e.is_file()]
        logger.info("Checking files in {import_path}.", import_path=self.import_path)
        for filepath in file_paths:
            image_metadata = self.get_metadata(filepath=filepath)
            logger.info("Checking file {filepath}.", filepath=filepath)
            if image_metadata["FileType"] == "ARW":
                if self.is_anamorphic(image_metadata=image_metadata):
                    logger.debug("Desqueezing {filepath}.", filepath=filepath)
                    self.desqueeze_file(filepath=filepath, image_metadata=image_metadata)
                    logger.debug("Desqueezing {filepath}. Done.", filepath=filepath)
                else:
                    logger.debug("{filepath} is not an anamorphic image.", filepath=filepath)

        logger.info("Checking files in {import_path}. Done.", import_path=self.import_path)
