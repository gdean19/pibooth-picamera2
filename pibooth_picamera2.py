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

# Check pibooth version
try:
    import pibooth
    LOGGER.info(f"Pibooth version: {pibooth.__version__}")
except:
    LOGGER.warning("Could not determine pibooth version")

# Try to import get_gp_camera_proxy
get_gp_camera_proxy = None
try:
    from pibooth.camera import get_gp_camera_proxy
    LOGGER.info("Successfully imported get_gp_camera_proxy from pibooth.camera")
except ImportError as e:
    LOGGER.error(f"Failed to import get_gp_camera_proxy: {e}")
    # Try alternative imports
    try:
        from pibooth.camera.plugin import get_gp_camera_proxy
        LOGGER.info("Found get_gp_camera_proxy in pibooth.camera.plugin")
    except:
        try:
            import pibooth.camera
            if hasattr(pibooth.camera, 'get_gp_camera_proxy'):
                get_gp_camera_proxy = pibooth.camera.get_gp_camera_proxy
                LOGGER.info("Found get_gp_camera_proxy as attribute of pibooth.camera")
            else:
                LOGGER.error("get_gp_camera_proxy not found in pibooth.camera")
                # List what's available
                LOGGER.info(f"Available in pibooth.camera: {dir(pibooth.camera)}")
        except Exception as e:
            LOGGER.error(f"Failed all import attempts: {e}")

# If we still don't have the function, create our own
if get_gp_camera_proxy is None:
    LOGGER.warning("Creating custom get_gp_camera_proxy function")
    def get_gp_camera_proxy():
        """Custom implementation of get_gp_camera_proxy"""
        try:
            import gphoto2 as gp
            # Initialize logging
            gp.check_result(gp.use_python_logging())
            
            # Create camera object
            camera = gp.Camera()
            
            # Initialize camera
            camera.init()
            
            LOGGER.info("Custom get_gp_camera_proxy: Camera initialized successfully")
            return camera
        except Exception as e:
            LOGGER.error(f"Custom get_gp_camera_proxy failed: {e}")
            return None

# Try alternative import method
try:
    import pibooth.camera.gphoto as gphoto_module
    LOGGER.info("Successfully imported pibooth.camera.gphoto module")
except ImportError as e:
    LOGGER.error(f"Failed to import pibooth.camera.gphoto: {e}")
    gphoto_module = None


# Release version
__version__ = "1.2.0"

@pibooth.hookimpl(tryfirst=True)
def pibooth_startup(app, cfg):
    """Called at pibooth startup to log plugin loading"""
    LOGGER.info("="*60)
    LOGGER.info("PICAMERA2 PLUGIN LOADED - Version %s", __version__)
    LOGGER.info("Plugin file: %s", __file__)
    LOGGER.info("Checking configuration:")
    LOGGER.info("  use_picamera2: %s", cfg.get('CAMERA', 'use_picamera2'))
    LOGGER.info("  use_picamera2_hybrid: %s", cfg.get('CAMERA', 'use_picamera2_hybrid'))
    LOGGER.info("  picamera2_gphoto2_direct: %s", cfg.get('CAMERA', 'picamera2_gphoto2_direct'))
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
    cfg.add_option('CAMERA', 'picamera2_gphoto2_direct', False,
                   "Try direct gphoto2 initialization if proxy fails (experimental)")
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
    
    # Check if we're on the main thread
    import threading
    LOGGER.info(f"Current thread: {threading.current_thread().name}")
    LOGGER.info(f"Is main thread: {threading.current_thread() is threading.main_thread()}")
    
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
                
            # Also try to list camera abilities
            LOGGER.info("Running gphoto2 --abilities to check camera capabilities...")
            result2 = subprocess.run(['gphoto2', '--abilities'], capture_output=True, text=True)
            if "Abilities for camera" in result2.stdout:
                LOGGER.info("gphoto2 can communicate with the camera")
            else:
                LOGGER.warning("gphoto2 cannot get camera abilities")
                
            # Kill any lingering gphoto2 processes that might be blocking
            LOGGER.info("Checking for processes that might block camera access...")
            try:
                # Check for gvfs-gphoto2-volume-monitor
                result3 = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
                if 'gvfs-gphoto2-volume-monitor' in result3.stdout:
                    LOGGER.warning("gvfs-gphoto2-volume-monitor is running - this can block camera access")
                    subprocess.run(['pkill', '-f', 'gvfs-gphoto2-volume-monitor'], capture_output=True)
                    LOGGER.info("Killed gvfs-gphoto2-volume-monitor")
                
                subprocess.run(['pkill', '-f', 'gphoto2'], capture_output=True)
                time.sleep(0.5)  # Give it time to clean up
                LOGGER.info("Cleaned up any lingering gphoto2 processes")
            except:
                pass
                
        except Exception as e:
            LOGGER.error(f"Could not run gphoto2 diagnostic: {e}")
        
        # Now try to get the proxy with detailed debugging
        LOGGER.info("Calling get_gp_camera_proxy()...")
        
        # Check what get_gp_camera_proxy actually is
        LOGGER.info(f"get_gp_camera_proxy function: {get_gp_camera_proxy}")
        LOGGER.info(f"get_gp_camera_proxy module: {get_gp_camera_proxy.__module__}")
        
        # Check if the function has any documentation
        if hasattr(get_gp_camera_proxy, '__doc__') and get_gp_camera_proxy.__doc__:
            LOGGER.info(f"get_gp_camera_proxy docstring: {get_gp_camera_proxy.__doc__}")
        
        # Check what's available in pibooth.camera module
        try:
            import pibooth.camera as cam_module
            LOGGER.info("Checking pibooth.camera module contents:")
            camera_attrs = [attr for attr in dir(cam_module) if not attr.startswith('_')]
            LOGGER.info(f"Available functions/classes: {camera_attrs}")
        except Exception as e:
            LOGGER.error(f"Could not inspect pibooth.camera module: {e}")
        
        # Add timing to see if it's a timeout issue
        import time
        start_time = time.time()
        
        try:
            # First try the standard method
            gp_cam_proxy = get_gp_camera_proxy()
            elapsed = time.time() - start_time
            LOGGER.info(f"get_gp_camera_proxy() completed in {elapsed:.2f} seconds")
            
            if gp_cam_proxy:
                LOGGER.info("✓ gPhoto2 proxy obtained successfully")
                LOGGER.info(f"  gPhoto2 camera type: {type(gp_cam_proxy)}")
                LOGGER.info(f"  Camera proxy attributes: {dir(gp_cam_proxy)}")
                # Try to get camera info
                try:
                    if hasattr(gp_cam_proxy, 'get_summary'):
                        summary = gp_cam_proxy.get_summary()
                        LOGGER.info(f"  Camera summary: {summary}")
                except Exception as e:
                    LOGGER.warning(f"  Could not get camera summary: {e}")
            else:
                LOGGER.warning("✗ get_gp_camera_proxy() returned None")
                
                # Try alternative method - direct gphoto2 initialization
                if cfg.get('CAMERA', 'picamera2_gphoto2_direct'):
                    LOGGER.info("Direct gphoto2 mode enabled - attempting alternative initialization...")
                    try:
                        import gphoto2 as gp
                        LOGGER.info("gphoto2 module imported successfully")
                        
                        # Initialize gphoto2
                        gp.check_result(gp.use_python_logging())
                        
                        # Try to get camera
                        camera = gp.Camera()
                        LOGGER.info("Created gp.Camera() instance")
                        
                        # Try to initialize
                        camera.init()
                        LOGGER.info("✓ Direct gphoto2 initialization successful!")
                        
                        # Set as proxy (this might not work with pibooth's GpCamera class)
                        gp_cam_proxy = camera
                        LOGGER.warning("Using direct gphoto2 camera - this may not be fully compatible")
                        
                    except Exception as e:
                        LOGGER.error(f"Alternative gphoto2 initialization failed: {e}")
                        gp_cam_proxy = None
                else:
                    LOGGER.info("Direct gphoto2 mode not enabled (set picamera2_gphoto2_direct = True to try)")
                
        except Exception as e:
            LOGGER.error(f"Exception during get_gp_camera_proxy(): {e}")
            import traceback
            LOGGER.error(traceback.format_exc())
            gp_cam_proxy = None
            
        if not gp_cam_proxy:
            LOGGER.warning("  Make sure:")
            LOGGER.warning("  1. DSLR is connected via USB")
            LOGGER.warning("  2. DSLR is turned ON")
            LOGGER.warning("  3. DSLR is in the correct mode (not Mass Storage)")
            LOGGER.warning("  4. You have permissions to access the camera")
            LOGGER.warning("  5. No other application is using the camera")
            LOGGER.warning("  6. gphoto2 is properly installed: sudo apt install gphoto2 libgphoto2-dev")
            
            # Check if we can import gphoto2 python module
            try:
                import gphoto2 as gp
                LOGGER.info("  ✓ python-gphoto2 module is installed")
            except ImportError:
                LOGGER.error("  ✗ python-gphoto2 module NOT installed!")
                LOGGER.error("    Install with: pip3 install gphoto2")
            
            # Check USB permissions
            try:
                import os
                LOGGER.info("Checking USB device permissions...")
                result = subprocess.run(['ls', '-la', '/dev/bus/usb/'], capture_output=True, text=True)
                if result.stdout:
                    LOGGER.info("USB devices:")
                    for line in result.stdout.splitlines()[:5]:  # First 5 lines
                        LOGGER.info(f"  {line}")
                    
                # Check if user is in the correct groups
                result = subprocess.run(['groups'], capture_output=True, text=True)
                LOGGER.info(f"Current user groups: {result.stdout.strip()}")
                if 'plugdev' not in result.stdout:
                    LOGGER.warning("User not in 'plugdev' group - this may cause permission issues")
                    LOGGER.warning("Fix with: sudo usermod -a -G plugdev $USER")
            except Exception as e:
                LOGGER.error(f"Could not check permissions: {e}")
    
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
        self._is_preview_active = False  # Track preview state
        
    def _specific_initialization(self):
        """Camera initialization.
        """
        resolution = self._transform()
        # Create preview configuration
        self._preview_config = self._cam.create_preview_configuration(main={'size':resolution}, 
                                transform=Transform(hflip=self.preview_flip))
        
        self._capture_config = self._cam.create_still_configuration(main={'size':resolution},
                                transform=Transform(hflip=self.capture_flip))
    
    def _is_camera_running(self):
        """Safely check if camera is running."""
        try:
            return hasattr(self._cam, '_preview') and self._cam._preview
        except:
            return False
    
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
        # Check if preview is already running
        if self._is_preview_active:
            LOGGER.info("Preview already active, skipping initialization")
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

        try:
            # Make sure camera is stopped before configuring
            try:
                self._cam.stop()
                time.sleep(0.1)
            except:
                pass  # Already stopped
                
            self._cam.configure(self._preview_config)
            self._cam.start()
            self._is_preview_active = True
            self.update_preview()
            LOGGER.info("Preview started successfully")
        except Exception as e:
            LOGGER.error(f"Error starting preview: {e}")
            self._is_preview_active = False
            raise

    def preview_countdown(self, timeout, alpha=60):
        """Show a countdown of 'timeout' seconds on the preview.
        Returns when the countdown is finished.
        Uses the same implementation as the parent but changes preview to _preview
        because of the difference between picamera and picamera2.
        """
        timeout = int(timeout)
        if timeout < 1:
            raise ValueError('Start time shall be greater than 0')
        if not self._is_preview_active:
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
        if not self._is_preview_active:
            LOGGER.warning("update_preview called but preview not active")
            return
            
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
            # Don't re-raise to avoid breaking the preview loop 

    def stop_preview(self):
        if not self._is_preview_active:
            LOGGER.info('Preview already stopped')
            return
            
        try:
            # Use method implemented in the parent class
            super().stop_preview()
            self._is_preview_active = False
            LOGGER.info('Stopped preview')
        except Exception as e:
            LOGGER.warning(f'Error stopping preview: {e}')
            
    def capture(self, effect=None):
        """Capture a new picture in a file.
        """
        effect = str(effect).lower()
        if effect not in self.IMAGE_EFFECTS:
            LOGGER.info(f'{effect} not in capture effects')
        if effect != 'none' and effect in self.IMAGE_EFFECTS:
            LOGGER.info(f'{self.__class__.__name__} has not been implemented with any effects')

        stream = BytesIO()
        
        try:
            # Stop preview if it's running
            if self._is_preview_active:
                LOGGER.info("Stopping preview for capture")
                self._cam.stop()
                self._is_preview_active = False
                time.sleep(0.1)
            
            # Configure and start in capture mode
            self._cam.configure(self._capture_config)
            self._cam.start()
            
            # Capture the image
            self._cam.capture_file(stream, format='jpeg')
            self._captures.append(stream)
            
            # Stop camera after capture
            self._cam.stop()
            time.sleep(0.1)  # Small delay to ensure camera is fully stopped
            
            LOGGER.info("Capture completed successfully")
                
        except Exception as e:
            LOGGER.error(f"Error during capture: {e}")
            # Try to recover
            try:
                self._cam.stop()
                self._is_preview_active = False
            except:
                pass
            raise
       

    def quit(self):
        """Close camera
        """
        try:
            if self._is_preview_active or self._is_camera_running():
                self._cam.stop()
                self._is_preview_active = False
        except:
            pass
        finally:
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
        
        # Stop Picamera2 if preview is active
        if self._is_preview_active:
            LOGGER.info("HybridPicamera2: Stopping Picamera2 preview before DSLR capture")
            try:
                self._cam.stop()
                self._is_preview_active = False
                time.sleep(0.2)  # Give more time for camera to fully stop
            except Exception as e:
                LOGGER.warning(f"HybridPicamera2: Error stopping Picamera2: {e}")
        else:
            LOGGER.info("HybridPicamera2: Picamera2 preview not active")
        
        # Capture with gPhoto2 camera
        LOGGER.info("HybridPicamera2: Triggering DSLR capture via gPhoto2")
        try:
            self._gp_cam.capture(effect)
            LOGGER.info("HybridPicamera2: DSLR capture successful")
        except Exception as e:
            LOGGER.error(f"HybridPicamera2: DSLR capture failed: {e}")
            # Try to restart preview on failure
            if self._window and self._preview_config and not self._is_preview_active:
                try:
                    self.preview(self._window)
                except:
                    pass
            raise
        
        # Hide overlay if it's still showing
        if self._overlay:
            self._hide_overlay()
        
        # Note: Don't restart preview here - let pibooth handle it
        LOGGER.info("HybridPicamera2: Capture complete, ready for next preview")
        
        # Small delay to ensure everything is settled
        time.sleep(0.1)

    def quit(self):
        """Close both camera drivers.
        """
        LOGGER.info("HybridPicamera2: Shutting down both cameras")
        super(HybridPicamera2, self).quit()
        self._gp_cam.quit()
        LOGGER.info("HybridPicamera2: Shutdown complete")


# Debug message at module load time
LOGGER.info("PICAMERA2 PLUGIN: Module loaded successfully")

# Additional debugging help
"""
TROUBLESHOOTING HYBRID MODE:

If the DSLR is detected by gphoto2 but not by pibooth:

1. Check pibooth version - some versions may have different implementations
2. Try setting picamera2_gphoto2_direct = True in config
3. Make sure python-gphoto2 is installed: pip3 install gphoto2
4. Check USB permissions: sudo usermod -a -G plugdev $USER
5. Kill blocking processes: pkill -f gvfs-gphoto2-volume-monitor
6. Test camera directly: gphoto2 --capture-image-and-download

The debug output will show:
- Whether get_gp_camera_proxy is available
- What functions are in pibooth.camera module
- Whether direct gphoto2 initialization works
- Any permission or process blocking issues

CAMERA STATE MANAGEMENT (v1.2.0):
- Added proper state tracking to prevent "Camera must be stopped before configuring" errors
- Picamera2 is properly stopped before DSLR captures
- Preview state is tracked with _is_preview_active flag
- Added delays between stop/start operations for stability
- Improved error recovery if camera gets into bad state
"""