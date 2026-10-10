import unittest
from unittest.mock import patch

from clipper import (
    center_crop_box,
    create_instagram_reel_container,
    get_video_duration,
    get_instagram_container_status,
    plan_clip_segments,
    publish_instagram_reel,
    generate_video_metadata_together,
    validate_clip_range,
    validate_public_video_url,
    validate_youtube_url,
)


class VideoInputTests(unittest.TestCase):
    def test_accepts_youtube_video_urls(self) -> None:
        self.assertEqual(
            validate_youtube_url("https://www.youtube.com/watch?v=abc"),
            "https://www.youtube.com/watch?v=abc",
        )
        self.assertEqual(
            validate_youtube_url("https://youtu.be/abc"), "https://youtu.be/abc"
        )

    def test_rejects_non_youtube_hosts(self) -> None:
        for url in (
            "https://youtube.com.example.org/watch?v=abc",
            "file:///video.mp4",
            "https://[invalid",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_youtube_url(url)

    def test_accepts_only_public_https_reel_video_urls(self) -> None:
        self.assertEqual(
            validate_public_video_url("https://cdn.example.org/clip.mp4"),
            "https://cdn.example.org/clip.mp4",
        )
        for url in (
            "http://cdn.example.org/clip.mp4",
            "https://localhost/clip.mp4",
            "https://127.0.0.1/clip.mp4",
            "https://user:pass@cdn.example.org/clip.mp4",
            "https://[invalid",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_public_video_url(url)

    def test_clip_range_requires_bounded_finite_values(self) -> None:
        validate_clip_range(0, 45)
        for start, duration in ((-1, 10), (float("nan"), 10), (0, 0), (0, 181)):
            with self.subTest(start=start, duration=duration), self.assertRaises(ValueError):
                validate_clip_range(start, duration)


class PortraitCropTests(unittest.TestCase):
    def test_wide_video_is_center_cropped_to_portrait_ratio(self) -> None:
        box = center_crop_box(1920, 1080)
        left, top, right, bottom = box
        self.assertEqual((top, bottom), (0, 1080))
        self.assertAlmostEqual((right - left) / (bottom - top), 9 / 16, places=2)
        self.assertLessEqual(abs(left - (1920 - right)), 1)

    def test_portrait_video_is_center_cropped_to_portrait_ratio(self) -> None:
        box = center_crop_box(1080, 1920)
        left, top, right, bottom = box
        self.assertEqual((left, right), (0, 1080))
        self.assertAlmostEqual((right - left) / (bottom - top), 9 / 16, places=2)
        self.assertLessEqual(abs(top - (1920 - bottom)), 1)

    def test_rejects_invalid_dimensions(self) -> None:
        with self.assertRaises(ValueError):
            center_crop_box(0, 1080)


class MultiClipTests(unittest.TestCase):
    def test_plans_consecutive_clips_and_short_final_clip(self) -> None:
        self.assertEqual(
            plan_clip_segments(100, 10, 30, 10),
            [(10, 30), (40, 30), (70, 30)],
        )
        self.assertEqual(plan_clip_segments(75, 0, 30, 10), [(0, 30), (30, 30), (60, 15)])

    def test_stops_at_maximum_and_rejects_invalid_remainder(self) -> None:
        self.assertEqual(
            plan_clip_segments(100, 0, 10, 3),
            [(0, 10), (10, 10), (20, 10)],
        )
        with self.assertRaises(ValueError):
            plan_clip_segments(10, 10, 5, 3)
        with self.assertRaises(ValueError):
            plan_clip_segments(0, 0, 5, 3)

    def test_reads_duration_from_real_video(self) -> None:
        from moviepy import ColorClip
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory(prefix="clipper-duration-test-") as directory:
            video_path = Path(directory) / "source.mp4"
            ColorClip(size=(160, 90), color=(20, 80, 140), duration=1).write_videofile(
                str(video_path), fps=12, codec="libx264", audio=False, logger=None
            )
            self.assertAlmostEqual(get_video_duration(video_path), 1, delta=0.1)


class TogetherAITests(unittest.TestCase):
    @patch("requests.post")
    def test_sends_three_sampled_frames_and_validates_metadata(self, post_mock) -> None:
        from moviepy import ColorClip
        from pathlib import Path
        from tempfile import TemporaryDirectory

        post_mock.return_value.ok = True
        post_mock.return_value.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"virality_score": 82, "virality_reason": "Strong visual hook", '
                            '"caption": "Watch this!", "hashtags": "#shorts #video"}'
                        )
                    }
                }
            ]
        }
        with TemporaryDirectory(prefix="clipper-together-test-") as directory:
            video_path = Path(directory) / "clip.mp4"
            ColorClip(size=(160, 90), color=(20, 80, 140), duration=1).write_videofile(
                str(video_path), fps=12, codec="libx264", audio=False, logger=None
            )
            metadata = generate_video_metadata_together(
                video_path, "Sample", "test-key"
            )

        self.assertEqual(metadata["virality_score"], 82)
        request = post_mock.call_args.kwargs
        self.assertEqual(request["json"]["model"], "Qwen/Qwen3.5-9B")
        content = request["json"]["messages"][0]["content"]
        images = [part for part in content if part["type"] == "image_url"]
        self.assertEqual(len(images), 3)
        self.assertTrue(all(part["image_url"]["url"].startswith("data:image/jpeg;base64,") for part in images))
        self.assertEqual(
            request["headers"]["Authorization"],
            "Bearer test-key",
        )


class InstagramPublishingTests(unittest.TestCase):
    @patch("requests.request")
    def test_create_container_uses_public_video_url(self, request_mock) -> None:
        request_mock.return_value.json.return_value = {"id": "container-123"}
        request_mock.return_value.ok = True

        container_id = create_instagram_reel_container(
            "account-456",
            "token",
            "v99.0",
            "https://cdn.example.org/clip.mp4",
            "A caption",
        )

        self.assertEqual(container_id, "container-123")
        args, kwargs = request_mock.call_args
        self.assertEqual(args[:2], ("POST", "https://graph.facebook.com/v99.0/account-456/media"))
        self.assertEqual(
            kwargs["data"],
            {
                "media_type": "REELS",
                "video_url": "https://cdn.example.org/clip.mp4",
                "caption": "A caption",
            },
        )
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer token"})

    @patch("requests.request")
    def test_processing_status_and_publish_use_same_container(self, request_mock) -> None:
        request_mock.return_value.json.return_value = {"status_code": "FINISHED"}
        request_mock.return_value.ok = True
        self.assertEqual(
            get_instagram_container_status("container-123", "token", "v99.0"),
            "FINISHED",
        )
        status_args, status_kwargs = request_mock.call_args
        self.assertEqual(
            status_args[:2],
            ("GET", "https://graph.facebook.com/v99.0/container-123"),
        )
        self.assertEqual(status_kwargs["params"], {"fields": "status_code,status"})

        request_mock.return_value.json.return_value = {"id": "media-789"}
        media_id = publish_instagram_reel(
            "account-456", "container-123", "token", "v99.0"
        )
        self.assertEqual(media_id, "media-789")
        publish_args, publish_kwargs = request_mock.call_args
        self.assertEqual(
            publish_args[:2],
            ("POST", "https://graph.facebook.com/v99.0/account-456/media_publish"),
        )
        self.assertEqual(publish_kwargs["data"], {"creation_id": "container-123"})


if __name__ == "__main__":
    unittest.main()
