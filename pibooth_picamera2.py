try:
    import pibooth
except Exception as e:
    print(e)
    exit()
# import picamera2
import pygame
import time
import cv2
import PIL


from libcamera import Transform
from io import BytesIO
from PIL import Image

from pibooth.utils import LOGGER
from pibooth.camera.rpi import RpiCamera
from pibooth.camera.gphoto import GpCamera
from pibooth.language import get_translated_text

# Try to import get_gp_camera_proxy
try:
    from pibooth.camera import get_gp_camera_proxy
    LOGGER.info("Successfully imported get_gp_camera_proxy from pibooth.camera")
except ImportError as e:
    LOGGER.error(f"Failed to import get_gp_camera_proxy: {e}")
    # Define a dummy function if import fails
    def get_gp_camera_proxy():
        LOGGER.error("get_gp_camera_proxy not available - import failed")
        return None


# Release version
__version__ = "1.1.0"

@pibooth.hookimpl(tryfirst=True)
def pibooth_startup(app, cfg):
    """Called at pibooth startup to log plugin loading"""
    LOGGER.info("="*60)
    LOGGER.info("PICAMERA2 PLUGIN LOADED - Version %s", __version__)
    LOGGER.info("Plugin file: %s", __file__)
    LOGGER.info("Checking configuration:")
    LOGGER.info("  use_picamera2: %s", cfg.get('CAMERA', 'use_picamera2'))
    LOGGER.info("  use_picamera2_hybrid: %s", cfg.get('CAMERA', 'use_picamera2_hybrid'))
    LOGGER.info("="*60)

@pibooth.hookimpl 
def pibooth_configure(cfg):
    """Declare new configuration options.
    """
    LOGGER.info("PICAMERA2 PLUGIN: Configuring options")
    cfg.add_option('CAMERA', 'use_picamera2', True,
                   "Boolean value to use Picamera2 library and the new raspberry pi camera v3")
    cfg.add_option('CAMERA', 'use_picamera2_hybrid', False,
                   "Boolean value to enable hybrid mode (Picamera2 for preview, gPhoto2 for capture)")
    LOGGER.info("PICAMERA2 PLUGIN: Configuration options added")

# This hook returns the custom camera proxy.
# It is defined here because yield statement in a hookwrapper with a 
# similar name is used to invoke it later. But check first if other cameras
# are installed or if the user specifically asks for this
@pibooth.hookimpl(tryfirst=True)  # Add tryfirst=True to ensure this runs before other camera plugins
def pibooth_setup_camera(cfg):
    
    LOGGER.info("="*60)
    LOGGER.info("PICAMERA2 PLUGIN: Starting camera setup")
    LOGGER.info("="*60)
    
    rpi_picamera2_proxy = None
    gp_cam_proxy = None
    
    # Check configuration values
    use_picamera2 = cfg.get('CAMERA','use_picamera2')
    use_hybrid = cfg.get('CAMERA','use_picamera2_hybrid')
    
    LOGGER.info(f"Configuration: use_picamera2={use_picamera2}, use_picamera2_hybrid={use_hybrid}")
    
    if use_picamera2:
        LOGGER.info("Attempting to get Picamera2 proxy...")
        rpi_picamera2_proxy = get_rpi_picamera2_proxy()
        if rpi_picamera2_proxy:
            LOGGER.info("✓ Picamera2 proxy obtained successfully")
        else:
            LOGGER.warning("✗ Failed to get Picamera2 proxy")
    
    if use_hybrid:
        LOGGER.info("Hybrid mode enabled - attempting to get gPhoto2 proxy...")
        
        # First check if gphoto2 can detect the camera
        try:
            import subprocess
            LOGGER.info("Running gphoto2 --auto-detect to check for cameras...")
            result = subprocess.run(['gphoto2', '--auto-detect'], capture_output=True, text=True)
            LOGGER.info("gphoto2 --auto-detect output:")
            for line in result.stdout.splitlines():
                LOGGER.info(f"  {line}")
            if result.stderr:
                LOGGER.warning(f"gphoto2 stderr: {result.stderr}")
        except Exception as e:
            LOGGER.error(f"Could not run gphoto2 diagnostic: {e}")
        
        # Now try to get the proxy
        gp_cam_proxy = get_gp_camera_proxy()
        if gp_cam_proxy:
            LOGGER.info("✓ gPhoto2 proxy obtained successfully")
            LOGGER.info(f"  gPhoto2 camera type: {type(gp_cam_proxy)}")
            # Try to get camera info
            try:
                if hasattr(gp_cam_proxy, 'get_summary'):
                    summary = gp_cam_proxy.get_summary()
                    LOGGER.info(f"  Camera summary: {summary}")
            except Exception as e:
                LOGGER.warning(f"  Could not get camera summary: {e}")
        else:
            LOGGER.warning("✗ Failed to get gPhoto2 proxy - check if DSLR is connected and turned on")
            LOGGER.warning("  Make sure:")
            LOGGER.warning("  1. DSLR is connected via USB")
            LOGGER.warning("  2. DSLR is turned ON")
            LOGGER.warning("  3. DSLR is in the correct mode (not Mass Storage)")
            LOGGER.warning("  4. You have permissions to access the camera")
            LOGGER.warning("  5. No other application is using the camera")
    
    if not rpi_picamera2_proxy:
        LOGGER.info('Could not find picamera2')
        LOGGER.info('Returning None - other camera plugins may take over')
        return None
    
    # Check if we have both cameras for hybrid mode
    if rpi_picamera2_proxy and gp_cam_proxy and use_hybrid:
        LOGGER.info("="*60)
        LOGGER.info("✓ HYBRID MODE: Both cameras detected!")
        LOGGER.info("  - Preview: Picamera2")
        LOGGER.info("  - Capture: gPhoto2 (DSLR)")
        LOGGER.info("="*60)
        hybrid_cam = HybridPicamera2(rpi_picamera2_proxy, gp_cam_proxy)
        LOGGER.info("Returning HybridPicamera2 instance")
        return hybrid_cam
    elif rpi_picamera2_proxy and use_hybrid and not gp_cam_proxy:
        LOGGER.warning("="*60)
        LOGGER.warning("! HYBRID MODE REQUESTED BUT DSLR NOT FOUND")
        LOGGER.warning("  Falling back to Picamera2 only mode")
        LOGGER.warning("="*60)
        picam = Rpi_Picamera2(rpi_picamera2_proxy)
        LOGGER.info("Returning Rpi_Picamera2 instance (fallback)")
        return picam
    elif rpi_picamera2_proxy:
        LOGGER.info("Configuring Picamera2 camera (standard mode)...")
        picam = Rpi_Picamera2(rpi_picamera2_proxy)
        LOGGER.info("Returning Rpi_Picamera2 instance")
        return picam
    
    LOGGER.warning("No camera configured by this plugin - returning None")
    return None


def get_rpi_picamera2_proxy():
    try:
        from picamera2 import Picamera2
        cam = Picamera2()
    except Exception as e:
        LOGGER.error(f"Failed to create Picamera2: {e}")
        cam = None 
    if cam:
        LOGGER.info('Use Picamera2 library')
        return cam
    return None 


class Rpi_Picamera2(RpiCamera):

    """Raspberry pi module v3 camera management
    """
    # Maximum resolution of the camera v3 module
    MAX_RESOLUTION = (4608,2592)
    IMAGE_EFFECTS = [u'none',
                     u'blur',
                     u'contour',
                     u'detail',
                     u'edge_enhance',
                     u'edge_enhance_more',
                     u'emboss',
                     u'find_edges',
                     u'smooth',
                     u'smooth_more',
                     u'sharpen']

    def __init__(self, camera_proxy):
        super().__init__(camera_proxy)
        self._preview_config = None
        self._capture_config = None
        
    def _specific_initialization(self):
        """Camera initialization.
        """
        resolution = self._transform()
        # Create preview configuration
        self._preview_config = self._cam.create_preview_configuration(main={'size':resolution}, 
                                transform=Transform(hflip=self.preview_flip))
        
        self._capture_config = self._cam.create_still_configuration(main={'size':resolution},
                                transform=Transform(hflip=self.capture_flip))
    
    def _show_overlay(self, text, alpha):
        """Add an image as an overlay
        """
        if self._window:
            # return a rect the size of the preview window(Keep overlay the same with
            # rotate=False)
            rect = self.get_rect(self.MAX_RESOLUTION, rotate=False)

            # Create an image padded to the required size
            size = (((rect.width + 31) // 32) * 32, ((rect.height + 15) // 16) * 16)

            # return a pil image with timeout on it
            image = self.build_overlay(size, str(text), alpha)

            # convert pil image to pygame.Surface
            self._overlay = pygame.image.frombuffer(image.tobytes(),size,'RGBA')
            self.update_preview()

    def _hide_overlay(self):
        """"""
        if self._overlay:
            self._overlay = None
            self.update_preview()

    def _post_process_capture(self,capture_data):

        img = super()._post_process_capture(capture_data)
        return self._rotate_image(img)
        

    def _transform(self):
        """Return tuple for configuring picamera"""
        if self.preview_rotation in (90,270):
            return self.resolution[1], self.resolution[0]
        else:
            return self.resolution
    
    def _rotate_image(self, image:PIL.Image.Image | pygame.Surface):
        """Rotate image clockwise"""
        # Camera rotation is the same for both preview and capture
        if self.capture_rotation != 0 and self.preview_rotation != 0:
            if isinstance(image, PIL.Image.Image):
                return image.transpose(getattr(Image,f'ROTATE_{self.capture_rotation}'))
            return pygame.transform.rotate(image,360-self.preview_rotation)
        return image
        
    def get_rect(self, max_size, rotate=True):
        """Get preview window. Rotate window to match camera rotation.
        Not implemented in picamera2. 
        """
        if self.preview_rotation in (90,270) and rotate:
            rect = super().get_rect(max_size)
            rect.width, rect.height = rect.height, rect.width 
            return rect
        return super().get_rect(max_size) 

    def preview(self, window, flip=True):
        if self._cam._preview:
            # Preview is still running
            return
        # create rect dimensions for preview window
        self._window = window
        
        # if the camera image has been flipped don't flip a second time
        # The flip overrides any previous flip value
        if self.preview_flip != flip:
            self.preview_flip = flip
            # if rotation is 90 or 270 degrees, vertically flip the image
            # when the image is rotated, the horizontally flipping is done
            # by vertically flipping it.
            if self.preview_rotation in (90,270):
                self._preview_config['transform'] = Transform(vflip=flip)
            else:
                self._preview_config['transform'] = Transform(hflip=flip)

        self._cam.configure(self._preview_config)
        self._cam.start()
        self.update_preview()

    def preview_countdown(self, timeout, alpha=60):
        """Show a countdown of 'timeout' seconds on the preview.
        Returns when the countdown is finished.
        Uses the same implementation as the parent but changes preview to _preview
        because of the difference between picamera and picamera2.
        """
        timeout = int(timeout)
        if timeout < 1:
            raise ValueError('Start time shall be greater than 0')
        if not self._cam._preview:
            raise RuntimeError('Preview shall be started first')
        time_stamp = time.time() 
        
        while timeout > 0:
            self._show_overlay(timeout, alpha)
            if time.time()-time_stamp > 1:
                timeout -= 1
                time_stamp = time.time()
                self._hide_overlay()
        # Keep smile for 1 second
        while time.time()-time_stamp < 1:
            self._show_overlay(get_translated_text('smile'), alpha)
        # Remove smile
        # _hide_overlay sets self._overlay = None otherwise app stalls after capture method is called
        self._hide_overlay()

    def preview_wait(self, timeout, alpha=60):
        time_stamp = time.time()
        # Keep preview for the duration of timeout
        while time.time() - time_stamp < timeout:
            self.update_preview()
        time_stamp = time.time()
        # Keep smile for 1 second
        while time.time()-time_stamp < 1:
            self._show_overlay(get_translated_text('smile'), alpha)
        self._hide_overlay()

    def update_preview(self):
        """Capture image and update screen with image"""
        try:
            array = self._cam.capture_array('main')
            rect = self.get_rect(self.MAX_RESOLUTION)
            # Resize high resolution image to fit smaller window
            res = cv2.resize(array, dsize=(rect.width,rect.height), 
                    interpolation=cv2.INTER_CUBIC)
            # RGBX is 32 bit and has an unused 8 bit channel described as X
            # XBGR is used in the preview configuration
            pg_image = pygame.image.frombuffer(res.data, 
                        (rect.width, rect.height), 'RGBX')
            pg_image = self._rotate_image(pg_image)
            screen_rect = self._window.surface.get_rect()
            self._window.surface.blit(pg_image,
                                    pg_image.get_rect(center=screen_rect.center))
            if self._overlay:
                self._window.surface.blit(self._overlay, self._overlay.get_rect(center=screen_rect.center))
            pygame.display.update()
        except Exception as e:
            LOGGER.error(f"Error updating preview: {e}")
            raise 

    def stop_preview(self):
        if self._cam._preview:
            # Use method implemented in the parent class
            super().stop_preview()
            LOGGER.info('Stopped preview')
            
    def capture(self, effect=None):
        """Capture a new picture in a file.
        """
        effect = str(effect).lower()
        if effect not in self.IMAGE_EFFECTS:
            LOGGER.info(f'{effect} not in capture effects')
        if effect != 'none' and effect in self.IMAGE_EFFECTS:
            LOGGER.info(f'{self.__class__.__name__} has not been implemented with any effects')

        stream = BytesIO()
        
        self._cam.switch_mode(self._capture_config)
        self._cam.capture_file(stream, format='jpeg')

        self._captures.append(stream)
        # Reconfigure and Stop camera before next preview
        self._cam.switch_mode(self._preview_config)
        self._cam.stop()
       

    def quit(self):
        """Close camera
        """
        self._cam.close()


class HybridPicamera2(Rpi_Picamera2):
    """Camera management using the Picamera2 for the preview (better
    video rendering) and a gPhoto2 compatible camera for the capture (higher
    resolution)
    """
    # Use gPhoto2 effects for capture
    IMAGE_EFFECTS = GpCamera.IMAGE_EFFECTS

    def __init__(self, rpi_picamera2_proxy, gp_camera_proxy):
        LOGGER.info("HybridPicamera2: Initializing hybrid camera")
        super(HybridPicamera2, self).__init__(rpi_picamera2_proxy)
        LOGGER.info("HybridPicamera2: Creating GpCamera instance")
        self._gp_cam = GpCamera(gp_camera_proxy)
        self._gp_cam._captures = self._captures  # Same dict for both cameras
        LOGGER.info("HybridPicamera2: Initialization complete")

    def initialize(self, *args, **kwargs):
        """Ensure that both cameras are initialized.
        """
        LOGGER.info("HybridPicamera2: Initializing both cameras")
        super(HybridPicamera2, self).initialize(*args, **kwargs)
        LOGGER.info("HybridPicamera2: Picamera2 initialized")
        self._gp_cam.initialize(*args, **kwargs)
        LOGGER.info("HybridPicamera2: gPhoto2 camera initialized")

    def _post_process_capture(self, capture_data):
        """Rework capture data.
        :param capture_data: couple (GPhotoPath, effect)
        :type capture_data: tuple
        """
        LOGGER.info(f"HybridPicamera2: Post-processing capture data: {capture_data}")
        return self._gp_cam._post_process_capture(capture_data)

    def capture(self, effect=None):
        """Capture a picture using gPhoto2 camera.
        """
        LOGGER.info(f"HybridPicamera2: Starting capture with effect={effect}")
        
        # Stop Picamera2 before gPhoto2 capture to avoid conflicts
        if self._cam._preview:
            LOGGER.info("HybridPicamera2: Stopping Picamera2 preview before DSLR capture")
            self._cam.stop()
        
        # Capture with gPhoto2 camera
        LOGGER.info("HybridPicamera2: Triggering DSLR capture via gPhoto2")
        try:
            self._gp_cam.capture(effect)
            LOGGER.info("HybridPicamera2: DSLR capture successful")
        except Exception as e:
            LOGGER.error(f"HybridPicamera2: DSLR capture failed: {e}")
            raise
        
        # Hide overlay if it's still showing
        self._hide_overlay()
        
        # Restart Picamera2 for next preview if window is available
        if self._window and self._preview_config:
            LOGGER.info("HybridPicamera2: Restarting Picamera2 for preview")
            self._cam.configure(self._preview_config)
            self._cam.start()

    def quit(self):
        """Close both camera drivers.
        """
        LOGGER.info("HybridPicamera2: Shutting down both cameras")
        super(HybridPicamera2, self).quit()
        self._gp_cam.quit()
        LOGGER.info("HybridPicamera2: Shutdown complete")


# Debug message at module load time
LOGGER.info("PICAMERA2 PLUGIN: Module loaded successfully")