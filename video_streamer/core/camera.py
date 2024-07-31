import time
import logging
import struct
import sys
import os
import io
import multiprocessing
import multiprocessing.queues
import requests

from typing import Union, IO, Tuple

from PIL import Image

try:
    from PyTango import DeviceProxy
except ImportError:
    logging.warning("PyTango not available.")


class Camera:
    def __init__(self, device_uri: str,
                 cam_type: str = None,
                 width: int = 0,
                 height: int = 0,
                 sleep_time: int = 0.05,
                 debug: bool = False
                 ):

        self._cam_type = cam_type
        self._connection_device = None
        self._valid_cam_types = ["mjpeg", "lima", "redis", "test"]
        self._device_uri = device_uri
        self._sleep_time = sleep_time
        self._debug = debug
        self._width = width
        self._height = height
        self._output = None
        self.testimg_fpath = None
        self._im = None
        self._raw_data = None
        self._last_frame_number = -1

    @property
    def cam_type(self):
        """Getter method for the camera type (cam_type) property"""
        return self._cam_type

    @cam_type.setter
    def cam_type(self, cam_type):
        if not self._cam_type in self._valid_cam_types:
            msg = f'Invalid camera type: {self._cam_type}. Supported options are {self._valid_cam_types}'
            raise ValueError (msg)
        else :
            logging.info(f"{self._camera_type} camera detected!" )
            self._cam_type = cam_type

    @property
    def device_uri(self):
        return self._device_uri

    @property
    def connection_device(self):
        """Getter method for the connection device"""
        return self._connection_device

    @connection_device.setter
    def connection_device(self, cam_type):
        """Setter method for the conection device based on camera type"""

        if self.cam_type == "lima":
            try:
                logging.info("Connecting to %s", self.device_uri)
                lima_tango_device = DeviceProxy(self.device_uri)
                lima_tango_device.ping()
            except Exception:
                logging.exception("")
                logging.info("Could not connect to %s, retrying ...", self.device_uri)
                sys.exit(-1)
            else:
                self._connection_device = lima_tango_device

        elif self.cam_type == "redis":
            import redis
            self._connection_device = redis.Redis(self.device_uri)

        else :
            pass

    
    @property
    def size(self) -> Tuple[float, float]:
        return (self._width, self._height)

    @property
    def width(self):
        """The width property."""
        return self._width
    @property
    def height(self):
        """The height property."""
        return self._height

    def get_frame_number (self):
        fn = None
        if self.cam_type == 'lima':
            fn = self.connection_device.video_last_image_counter
        elif self.cam_type == "redis":
            fn = self.connection_device.get("last_image_id")
        return fn


    def _poll_once(self) -> None:
        if self.cam_type == "test":
            self._sleep_time = 0.05
            testimg_fpath = os.path.join(os.path.dirname(__file__), "fakeimg.jpg")
            self._im = Image.open(testimg_fpath, "r")

            self._raw_data = self._im.convert("RGB").tobytes()
            self._width, self._height = self._im.size
            self._write_data(self._raw_data)
            time.sleep(self._sleep_time)

        elif self.cam_type in ['lima', 'redis']:
            frame_number = self.get_frame_number()

            if self._last_frame_number != frame_number:
                raw_data, width, height, frame_number = self._get_image()
                self._raw_data = raw_data

                self._write_data(self._raw_data)
                self._last_frame_number = frame_number

            time.sleep(self._sleep_time / 2)


    def _write_data(self, data: bytearray):
        if isinstance(self._output, multiprocessing.queues.Queue):
            self._output.put(data)
        else:
            self._output.write(data)


    def poll_image(self, output: Union[IO, multiprocessing.queues.Queue]) -> None:
        self._output = output

        while True:
            try:
                self._poll_once()
            except KeyboardInterrupt:
                sys.exit(0)
            except BrokenPipeError:
                sys.exit(0)
            except Exception:
                logging.exception("")
            finally:
                pass


    def get_jpeg(self, data, size=(0, 0)) -> bytearray:
        jpeg_data = io.BytesIO()
        image = Image.frombytes("RGB", self.size, data, "raw")

        if size[0]:
            image = image.resize(size)

        image.save(jpeg_data, format="JPEG")
        jpeg_data = jpeg_data.getvalue()

        return jpeg_data


    def poll_image(self, output: Union[IO, multiprocessing.queues.Queue]) -> None:
        r = requests.get(self.device_uri, stream=True)

        buffer = bytes()
        while True:
            try:
                if r.status_code == 200:
                    for chunk in r.iter_content(chunk_size=1024):
                        buffer += chunk

                else:
                    print("Received unexpected status code {}".format(r.status_code))
            except requests.exceptions.StreamConsumedError:
                output.put(buffer)
                r = requests.get(self.device_uri, stream=True)
                buffer = bytes()

    def get_jpeg(self, data, size=None) -> bytearray:
        return data

    def _get_image(self) -> Tuple[bytearray, float, float, int]:
        if self.cam_type == "lima":

            img_data = self.connection_device.video_last_image

            hfmt = ">IHHqiiHHHH"
            hsize = struct.calcsize(hfmt)
            _, _, img_mode, frame_number, width, height, _, _, _, _ = struct.unpack(
                hfmt, img_data[1][:hsize]
            )

            raw_data = img_data[1][hsize:]

            return raw_data, width, height, frame_number

        elif self.cam_type == "redis":
            raw_data = self.connection_device.get("last_image_data")
            frame_number = self.connection_device.get("last_image_id")
            width, height = self.width, self.height

            return raw_data, width, height, frame_number
