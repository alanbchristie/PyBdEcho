"""PyBdEcho.py

The PyBoard 'Echo' audio capture/playback demonstration code.

https://github.com/alanbchristie/PyBdEcho

Refer to project's LICENCE.txt for licence restrictions.

v1.0.1 was presented at EuroPython, Rimini, July 2017.

This application is designed to run on the MicroPython PyBoard and
was developed using the following: -

-   MicroPython (tested with v1.9.2)
-   PyBoard v1.1
-   The AMP Audio skin v1.0.

The entry point is `echo()`, which simply invokes `_init()` followed by
`_capture_play_loop()`.

Alan B. Christie
July 2017
"""

from array import array
import micropython
import pyb
import sys
import utime

# --------------------------
# Emergency Exception Buffer
# --------------------------
# We're using timer-based callbacks so MicroPython requires us to
# create an emergency exception buffer. This enables MicroPython
# to produce an error report if an error occurs in the function.
micropython.alloc_emergency_exception_buf(100)

# --------------
# User Constants
# --------------

# Capture/playback resolution bits (8 or 12).
CAPTURE_BITS = 8

# The capture rate.
# Samples are read from the ADC at this rate.
CAPTURE_FREQUENCY_HZ = 8000

# The playback rate.
# Samples are written to the DAC at this rate.
# If `USE_OVER_SAMPLE_PLAYBACK` the playback frequency is set to
# 2x the capture frequency and this value is not used.
PLAYBACK_FREQUENCY_HZ = 12000

# The 'over-sample' flag signals the `_play()` function to over-sample
# the data when it's written to the DAC. Oversampling presents the data
# at twice the recording rate while also interpolating the values
# in the sub-sample slots (averaging the last and next value during
# every other operation. When oversampling the `_play()` function
# uses the dedicated 'over-sample' function because, at 16kHz we're close
# to the limits in what we can do in the function, even having conditional
# blocks pushes the function beyond the real-time limits.
USE_OVER_SAMPLE_PLAYBACK = True

# Size of the Speech Detection Buffer (SDB) (milliseconds).
# This is the circular buffer used by the `_capture_function()`
# while it's listening for speech.
SDB_SIZE_MS = 500

# Size of the Speech Buffer (SB) (seconds).
# Once speech has been detected samples are written to this buffer
# until full or end-of-speech has been detected
# by the `_capture_function()`. It has to be larger than the
# speech detection buffer, which is copied over the start
# of this buffer prior to playback.
# If using 'attenuation' (see below) then...
# ...at 8kHz & 12-bit resolution we have enough memory for a 3-second buffer.
# ...at 8kHz & 8-bit resolution we have enough memory for a 7-second buffer.
SB_SIZE_S = 7

# A 'frame' for the purpose of identifying areas of the speech buffer
# that contain only 'silence' samples. The concept of frames is used
# during end-of-speech detection and silence attenuation.
FRAME_PERIOD_MILLIS = 100

# Loudspeaker volume.
# 0 (off) to 127 (maximum).
LS_VOLUME = 127

# Speech threshold - the absolute difference between the silence estimate
# and a sample for it to be considered speech. There's a lot of noise
# on my board so expect +/- 156 at 8kHz, 12-bit (or 10 at 8-bit).
# Currently the estimate is not modified as recordings are made
# (although it could be adapted during attenuation).
# This value is 2 x standard deviation of typical noise levels.
#
# See `DETECTION_SAMPLES_PCENT`
if CAPTURE_BITS == 8:
    SPEECH_THRESHOLD = 25
else:
    SPEECH_THRESHOLD = 400

# Speech detection volume (percent).
# The proportion of the number of speech samples observed during the
# speech detection phase that are required to trigger a recording.
# Measured as a percentage size of the speech-detection buffer and
# should probably be greater than 5%.
#
# See `SPEECH_THRESHOLD`
DETECTION_SAMPLES_PCENT = 10

# Estimate of the sample value for silence
# (in an ideal world this would be 2048 for 12-bit data and 127 for 8-bit).
# This is just a default seed for the `adc_zero` value, which is adjusted
# during attenuation, a process that occurs immediately prior to playback.
# My board seems to settle around a value of 1893 at 12-bits (118 at 8 bits).
# Your starting value might be different depending on amp-skin resistor
# tolerances (see R11 & R13).
# `adc_zero` is not modified if attenuation is disabled.
if CAPTURE_BITS == 8:
    SILENCE = 127
else:
    SILENCE = 2048

# How many consecutive frames of silence need to occur after
# speech has been detected in order to decide that speech has finished?
# Keep short for best response times.
EOS_CONSEC_SILENCE_FRAMES = 300 // FRAME_PERIOD_MILLIS
# Must not be less than 1...
if EOS_CONSEC_SILENCE_FRAMES == 0:
    EOS_CONSEC_SILENCE_FRAMES = 1

# The 'toggle-rate' of the green LED when 'On Hold'.
# This is the period between 'on' and 'off' states of the LED when
# recording has been put 'on hold' with the USER button.
USER_BUTTON_TOGGLE_MS = 750

# Capture/playback status poll period.
# This is the periodic sleep time while waiting for capture/playback
# callback processes to finish.
CALLBACK_PAUSE_MS = 250

# The path of the root of an attached SD card.
SD_ROOT = '/sd'

# -----------------
# Derived constants
# -----------------
# Values derived from the above constants.
# Don't edit these, just edit the corresponding constant(s).

# Speech detection buffer size (in samples)
SDB_SAMPLE_SIZE = SDB_SIZE_MS * CAPTURE_FREQUENCY_HZ // 1000

# Speech buffer size (in samples).
SB_SAMPLE_SIZE = SB_SIZE_S * CAPTURE_FREQUENCY_HZ

# Absolute number of speech samples required to occupy the
# speech detection buffer for the buffer to be considered to
# contain the start of speech.
SPEECH_DETECTION_SAMPLE_THRESHOLD = SDB_SAMPLE_SIZE * \
                                    DETECTION_SAMPLES_PCENT // 100

# The number of samples in a frame...
FRAME_PERIOD_SAMPLES = CAPTURE_FREQUENCY_HZ * FRAME_PERIOD_MILLIS // 1000

# The number of frames in the speech buffer (will/must be whole)
SB_FRAME_COUNT = SB_SAMPLE_SIZE // FRAME_PERIOD_SAMPLES

# -----------------
# Control variables
# -----------------
# All the capture/playback control variables (globals, sorry)...

# A flag, toggled by the USER push-button.
# When True the capture/playback loop pauses at the next capture
# (and any current capture is forced to stop).
# The default state is True, so the user has to press the button
# to start the capture/playback loop when the board 'wakes up'.
on_hold = True

# The capture control flag.
# The flag is set by the main loop to start capturing and is cleared by the
# `_capture_function()` when it is complete.
capture = False

# The 'detect speech' flag.
# This flag controls whether the `_capture_function()` is detecting speech
# (writing to the circular speech detection buffer) or recording (to the
# speech buffer). Initially True, it is set to False in the
# `_capture_function()` when speech has been detected.
# It is returned to True at the end of each recording so that the next
# recording starts speech detection.
detect_speech = True

# The playback control flag.
# The flag is set by the main loop to start playing and is cleared by the
# chosen 'playback' function (there's more than one) when it is complete.
playback = False

# ---------------
# Other variables
# ----------------
# Other, miscellaneous variables (globals, sorry)...

# Capture function's Speech Sample Count (ssc).
# The number of samples in the speech detection buffer (and speech buffer)
# considered speech.
#
# During speech detection it is incremented for each speech sample collected
# that appears to be a speech sample and decremented (to zero)
# for each non-speech sample. When it meets the DETECTION_SAMPLE_THRESHOLD
# then someone has started talking and the `_capture_function()` moves to
# writing to the speech buffer.
#
# When recording starts the value is reset at the start of each `frame`
# and is used to count the number of speech samples in the current frame
# in order to identify 'quiet' frames for the purpose of detecting the
# end of speech.
ssc = 0

# The sub-sample variable is used by the `_over_sample_playback_function()`
# when we are 'over-sampling' the playback. The variable is incremented
# during playback and iterates through the values 0 and 1. When 0 a new
# sample is written to the DAC, when 1 an interpolated sample is written
# to the DAC.
sub_sample = 0

# Used by the `_capture_function()` to count the number of consecutive frames
# that have been found to be 'silent' after the speech recording has started.
# When this reaches `EOS_CONSEC_SILENCE_FRAMES` the `_capture_function()`
# considers speech to have ended and recording stops, putting the index
# of the last sample that needs to be replayed into the `eos_index` variable.
num_consec_post_speech_silence_frames = 0

# The _end of speech_ index. If 'end of speech' is not detected this
# is set to the extend of the speech buffer (i.e. `SB_SAMPLE_SIZE`).
#
# When end-of-speech has been detected this is the first sample in the
# _frame_ that  begins the discovered consecutive sequence of silence frames in
# the speech buffer. This value is used by the chosen 'playback' function()
# to stops playing the audio.
eos_index = SB_SAMPLE_SIZE

# The 'end of speech' (eos) flag. Used inside the `_capture_function()`
# this is set when it has detected the end of speech once recording
# has started. If no end of speech is detected it is set when the end of the
# speech buffer has been reached.
#
# When set the `_capture_function()` stops recording.
eos = False

# The 'estimate' ADC value that represents silence (zero).
# The value is adjusted during the attenuation phase,
# which run (if not disabled) after recordings have been made.
adc_zero = SILENCE

# The current speech-detection buffer write offset. A circular offset
# used by `_capture_function()`. Updated from within `_capture_function()`.
sdb_wr_offset = 0

# When we've detected speech we switch from writing to the speech detection
# buffer to writing to the main speech buffer. The offset accommodates a copy
# of the speech detection buffer, which is copied over the start of the speech
# buffer prior to playback.
#
# The `sb_wr_offset` is updated from within `_capture_function()`
# and reset to this value once recording has finished.
sb_wr_offset = SDB_SAMPLE_SIZE

# The 'read' offset into the speech buffer for samples being played back.
# This is initialised to zero and is used by the `_playback_function()`
# to read samples from the speech buffer and write them to the DAC,
# until the end-of-speech index has been reached.
sb_rd_offset = 0

# The initialisation state.
# Set after `_init()` has completed successfully.
initialised = False

# ---------------------------
# MicroPython/PyBoard objects
# ---------------------------
# The numerous PyBoard objects, timers, LEDs etc. Objects that need
# configuration after construction are configured inside `_init()`.

# The capture timer. This is used to invoke our `_capture_function()`
# at the designated SAMPLE_FREQUENCY_HZ.
# Configured in `_init()` and the function attached ans detached
capture_timer = pyb.Timer(14)

# The playback timer. This is used to invoke our chosen 'playback' function
# at the required rate, which is PLAYBACK_FREQUENCY_HZ (or
# 2 x CAPTURE_FREQUENCY_HZ if we're over-sampling the playback).
# Configured in `_init()`.
playback_timer = pyb.Timer(13)

# LED objects
red_led = pyb.LED(1)
grn_led = pyb.LED(2)
amb_led = pyb.LED(3)
blu_led = pyb.LED(4)

# ADC (Microphone) and DAC (loudspeaker)
adc = pyb.ADC(pyb.Pin.board.X22)
dac = pyb.DAC(1, bits=CAPTURE_BITS)
# Switch object. During initialisation this will be used
# to attach a handler function (`_user_switch_callback`) for the USER switch.
sw = pyb.Switch()

# Hardware timing pins.
# This pin voltage is lowered on entry to the time-critical capture and
# playback functions and raised on exit form the function.
# Attach an oscilloscope to these pins to measure
# the collection or playback callback duration.
capture_timing_pin = pyb.Pin(pyb.Pin.board.Y1, pyb.Pin.OUT_PP)
playback_timing_pin = pyb.Pin(pyb.Pin.board.Y2, pyb.Pin.OUT_PP)

# ------------------------------
# Audio storage (sample buffers)
# ------------------------------
# Buffers to store captured audio samples, depending on chosen sample
# resolution (8 or 12 bits).
#
# The size of the arrays will be set by appending zeros during `_init()`.
# We need one for the circular 'speech detection' buffer.
# We need one to record the 'speech' to once speech has been detected.

if CAPTURE_BITS == 8:
    sd_buf = array('B')
    s_buf = array('B')
else:
    sd_buf = array('H')
    s_buf = array('H')

# ---------------------------------
# Silence attenuation configuration
# ---------------------------------

# Enabled?
ATTENUATE_SILENCE = True

# Speech threshold during attenuation - the absolute difference between the
# silence estimate and a sample for it to be considered speech during
# an attenuation frame. This is normally higher than the SPEECH_THRESHOLD
# so we only attenuate if we're really sure it's not speech.
# for 12-bit 8kHz recordings I use a value of around 500 (and 8-bit is this
# value divided by 16 - i.e. the magnitude of the change in sample size at each
# resolution)
if CAPTURE_BITS == 8:
    ATTENUATE_SPEECH_THRESHOLD = 50     # Trial & Error
else:
    ATTENUATE_SPEECH_THRESHOLD = 800    # Trial & Error

# The percentage of samples that need to be _speech_ in a speech-buffer
# frame to prevent it from being attenuated. This is somewhat lower
# than the corresponding speech detection threshold so only a few samples
# need to represent speech to prevent the fame from being attenuated.
# It's better to attenuate when we're _really_ sure it's silence because
# attenuating to quickly or too close to speech can be disconcerting for
# the listener.
ATTENUATION_SAMPLES_PCENT = 1

# Frame period samples required to be speech
# before the frame is considered part of speech.
ATTENUATION_SPEECH_SAMPLE_THRESHOLD = FRAME_PERIOD_SAMPLES * \
                                      ATTENUATION_SAMPLES_PCENT // 100

# An array to hold a list of the first sample index of silent frames.
# Used during a 2nd-pass in attenuation to quickly attenuate silent
# frames found in the 1st-pass. If attenuation is enabled this array is sized
# by pre-populating zero values in `_init()`.
silent_frames = array('I')

# --------------------------------
# Configuration of diagnostic dump
# --------------------------------
# Dump collections to a connected SD card.

# Set to write collected data to an attached SD card.
# Writing to the SD card will only take place if it looks like
# there's an SD card present.
#
# Incidentally ... You will need to hard-reset the card
# before you can see any written files.
DUMP_TO_SD_CARD = False

# The maximum number of capture files to maintain.
# The files are used on a round-robin basis by writing
# to capture file 1, then capture file 2, etc.
DUMP_FILE_LIMIT = 50

# The next capture file number.
# incremented in a circular fashion in `_dump_capture_info()`.
dump_file_num = 1


# -----------------------------------------------------------------------------
def _init():
    """Initialise the application data and hardware objects.
    If initialisation fails, i.e. it can't set the loudspeaker volume,
    it returns None. If initialisation fails the capture-playback loop
    will not run.

    If already initialised this function does nothing.
     
    Returns 'True' if successfully initialised.
    """

    global initialised

    # Do nothing if already initialised
    if initialised:
        return

    print('Initialising...')

    # Sanity-check on capture resolution
    if CAPTURE_BITS not in [8, 12]:
        print('CAPTURE_BITS must be 8 or 12, not {}'.format(CAPTURE_BITS))
        return
    if SB_SAMPLE_SIZE <= SDB_SAMPLE_SIZE:
        print('SB_SAMPLE_SIZE must be greater than SDB_SAMPLE_SIZE')
        return

    # Set loud-speaker volume.
    # This may fail if there are problems with the board.
    if not _set_volume(LS_VOLUME):
        print('set_volume({}) failed.'
              ' Is the Audio Skin attached?'.format(LS_VOLUME))
        return

    grn_led.on()    # Lit when listening (flashing when 'on hold')
    amb_led.off()   # Lit when writing to the speech buffer
    blu_led.off()   # Lit when playing back the speech buffer
    red_led.off()   # Lit when writing to SD card/flash

    # Initialise the hardware timing pins (set them to 'high').
    capture_timing_pin.high()
    playback_timing_pin.high()

    # Create each capture array
    # by appending the appropriate number of samples...
    for _ in range(SDB_SAMPLE_SIZE):
        sd_buf.append(0)
    for _ in range(SB_SAMPLE_SIZE):
        s_buf.append(0)
    # Create the attenuator's frame sample array
    # (if we're attenuating)...
    if ATTENUATE_SILENCE:
        for _ in range(SB_FRAME_COUNT):
            silent_frames.append(0)

    # Create a timer we attach our collect function when we `listen`.
    # The function will do nothing while 'capture' is False.
    capture_timer.init(freq=CAPTURE_FREQUENCY_HZ)
    # Same with the playback function...
    # If we're over-sampling the playback the playback frequency
    # is set to 2x the capture frequency and PLAYBACK_FREQUENCY_HZ
    # is not used.
    if USE_OVER_SAMPLE_PLAYBACK:
        playback_timer.init(freq=CAPTURE_FREQUENCY_HZ * 2)
    else:
        playback_timer.init(freq=PLAYBACK_FREQUENCY_HZ)

    # Attach a service function that will handle the USER switch being hit.
    # The supplied function simply toggles the `on_hold` flag.
    sw.callback(_user_switch_callback)

    initialised = True

    print('Initialised.')

    return True


# -----------------------------------------------------------------------------
def _user_switch_callback():
    """Called in response to the USER switch being depressed.
    When 'on-hold' (not listening) the green LED flashes.
    When listening the green LED is continuously lit.
    """

    global on_hold

    # Just toggle the 'on hold' state
    if on_hold:
        on_hold = False
    else:
        on_hold = True


# -----------------------------------------------------------------------------
def _dump_capture_info():
    """Dumps capture data and timing statistics to a file.
    This only acts if dumping has been enabled and if an SD card
    is present.
    """

    global dump_file_num

    # Do nothing if not enabled.
    if not DUMP_TO_SD_CARD:
        return
    # Do not capture if it looks like there's no SD card.
    if SD_ROOT not in sys.path:
        print('DUMP_TO_FILE is set but there is no SD card.')
        return

    # Indicate we're writing to the SD card
    # by lighting the red LED...
    red_led.on()

    # Construct the intended dump file name...
    dump_file = '{}/PyBdEcho.{}.txt'.format(SD_ROOT, dump_file_num)
    # What's the next file number? (1..N)
    dump_file_num += 1
    if dump_file_num > DUMP_FILE_LIMIT:
        dump_file_num = 1

    # Open, write, close...

    print('Dumping to {}...'.format(dump_file))
    fp = open(dump_file, 'w')

    fp.write("adc_zero {}\n".format(adc_zero))
    fp.write("sb_wr_offset {}\n".format(sb_wr_offset))
    fp.write("sb_rd_offset {}\n".format(sb_rd_offset))
    fp.write("sdb_wr_offset {}\n".format(sdb_wr_offset))
    fp.write("eos_index {}\n".format(eos_index))

    fp.write("sdb->\n")
    for i in range(SDB_SAMPLE_SIZE):
        value = sd_buf[i]
        fp.write("{}\n".format(value))

    fp.write("sb->\n")
    for i in range(eos_index):
        value = s_buf[i]
        fp.write("{}\n".format(value))

    fp.close()

    print('Dumped.')

    # Indicate end of file operations...
    red_led.off()


# -----------------------------------------------------------------------------
def _set_volume(volume):
    """Sets the loudspeaker volume. Range is 0 (off) to 127.
    
    Returns False on error - usually an indication of a missing audio skin.
    If this fails the `_init()` should also fail, preventing the main
    application from running.
    
    Parameters
    ----------
    volume -- The volume 0..127 (int). Any other values are ignored.
    
    Returns False on failure
    """

    if volume < 0 or volume > 127:
        return

    try:
        pyb.I2C(1, pyb.I2C.MASTER).mem_write(volume, 46, 0)
    except OSError as e:
        print('ERROR: OSError {}'.format(e))
        return False

    # OK if we get here
    return True


# -----------------------------------------------------------------------------
def _capture():
    """Initiates a capture sequence by unlocking the `_capture_function()`.
    We then sit here waiting for the capture to finish.
    """

    global capture

    # To unlock the capture function (which then runs as a Timer-driven
    # callback) we set the `capture` flag and attach the `_capture_function()`
    # and wait until the flag gets cleared (by the `_capture_function()`).

    # Listening...
    grn_led.on()
    capture = True
    capture_timer.callback(_capture_function)

    while capture:
        utime.sleep_ms(CALLBACK_PAUSE_MS)

    # Detach the callback.
    # No point in having it run if we're not listening,
    # especially if we're playing back audio.
    capture_timer.callback(None)


# -----------------------------------------------------------------------------
def _play():
    """Plays the speech buffer (sb) to the loudspeaker (DAC)
    by unlocking a 'playback' function. The function will unlock either the
    standard playback function (`_playback_function()`) or the over-sampling
    playback function (`_over_sample_playback_function()`.

    We then sit here waiting for the chosen playback to finish.
    
    The caller must ensure that the speech-detection buffer
    has been copied into the spare space at the start of the speech
    buffer.
    """

    global playback

    # To initiate playback we set the `playback` control variable
    # and then attach the required playback function to a suitable timer.
    # We then simply need to wait until the `playback` variable has been
    # cleared (which the `_playback_function()` will do when
    # the buffer's been exhausted).

    playback = True

    # Over-sample the playback?
    # If so the `playback_timer` will be preset to 2x the capture frequency
    if USE_OVER_SAMPLE_PLAYBACK:
        playback_timer.callback(_over_sample_playback_function)
    else:
        playback_timer.callback(_playback_function)

    # Wait for playback to complete...
    while playback:
        utime.sleep_ms(CALLBACK_PAUSE_MS)

    # Detach the callback.
    # No point in having it run if we're not playing.
    # especially if we're capturing.
    playback_timer.callback(None)

    # Need to stop the DAC,
    # to silence its annoying 'whistle'
    _stop()


# -----------------------------------------------------------------------------
def _stop():
    """Stops the DAC (re-initialising it). We basically do this to keep the
    loudspeaker quiet after playback as the DAC does continue to make a
    rather annoying 'whistle' if left running.
    """

    dac.init(bits=CAPTURE_BITS)


# -----------------------------------------------------------------------------
def _copy_speech_detection_buffer():
    """The speech detection buffer is copied over the start of the speech
    buffer. The front of the speech buffer has sufficient space to hold the
    entire speech-detection buffer.
    
    The speech-detection buffer is circular in nature, the speech buffer is
    not and so the detection buffer is _unrolled_ over the start of
    the the speech buffer, from last written sample back to the first.
    """

    # The last written sample in the speech detection buffer
    # is at index `sdb_wr_offset - 1`. We unroll the speech detection buffer
    # over the start of the speech buffer, backwards, starting with this sample
    # value. `to_index` moves backwards through the speech buffer and
    # `from_index` works back through the speech detection buffer
    # (in a reverse circular fashion from the last sample value).

    from_index = sdb_wr_offset      # Don't worry - we pre-decrement shortly...
    for to_index in range(SDB_SAMPLE_SIZE - 1, -1, -1):
        from_index -= 1
        if from_index < 0:
            from_index = SDB_SAMPLE_SIZE - 1
        s_buf[to_index] = sd_buf[from_index]


# -----------------------------------------------------------------------------
def _attenuate_sb_silence():
    """Attempts to attenuate areas of the speech buffer that are silent,
    up to (but not including) the `eos_index`.
    
    This function does a number of things. Firstly, it calculates a new ADC
    silence level from the average value found across all 'frames' that are
    thought to represent silence. It then makes a second pass trough
    the speech buffer setting all the silent frames to the new ADC average.
    
    This method can be disabled by setting ATTENUATE_SILENCE to False.
    """

    global adc_zero

    # Do nothing if disabled
    if not ATTENUATE_SILENCE:
        return

    # Search each 'frame' from the start of the speech buffer.
    # If the frame is silent then accumulate all the samples in it.
    # At the end we calculate a new ADC zero from all the collected samples
    # and set all the samples in each silent frame we found to this new 'zero'.

    silence_sum = 0                 # Sum of all sample values in silent frames
    silence_sample_count = 0        # Total number of silent samples

    frame_sample_sum = 0            # Sum of samples in the current frame
    num_frame_speech_samples = 0    # Number of speech samples in current frame
    frame_is_silent = True          # True if the current frame is silent

    silent_frame_index = 0          # Next index into silent_frames array
    num_silent_frames = 0           # Number of silent frames

    # Run over the whole speech buffer (plus one sample).
    # allowing an index of the last sample lets us handle the last possible
    # frame without 'special case' logic.
    sample_index = 0
    while sample_index < eos_index + 1:

        # Starting a new frame?
        if sample_index % FRAME_PERIOD_SAMPLES == 0:
            # If we've started a new frame, was the previous silent?
            frame_sample_start = sample_index - FRAME_PERIOD_SAMPLES
            if sample_index > 0 and frame_is_silent:
                # Yep - it was a silent frame.
                # Accumulate the samples.
                silence_sum += frame_sample_sum
                silence_sample_count += FRAME_PERIOD_SAMPLES
                # And record the start of the frame
                # (so we can return to it later to attenuate it once we have
                # a new estimate for the silent sample value, i.e. `adc_zero`).
                silent_frames[silent_frame_index] = frame_sample_start
                silent_frame_index += 1
                num_silent_frames += 1
            # Break out if we've just stepped out of the speech buffer
            # (we've just analysed the last frame)
            if sample_index == eos_index:
                break
            # Otherwise - we're starting a frame.
            # Reset the frame sample sum
            # and assume it is going to be silent...
            num_frame_speech_samples = 0
            frame_sample_sum = 0
            frame_is_silent = True

        # Get the next sample from the frame.
        # Is it a silent sample? (compared to the existing `adc_zero`).
        # Once this function completes we may have a new `adc_zero` to
        # use next time we attenuate.
        sample = s_buf[sample_index]
        sample_index += 1
        delta = sample - adc_zero
        if delta < 0:
            delta *= -1
        if delta >= ATTENUATE_SPEECH_THRESHOLD:
            # Any speech-sized sample might prevent this frame
            # from being considered silent. Once we reach the
            # ATTENUATION_SPEECH_SAMPLE_THRESHOLD in a frame
            # then it is not a silent frame.
            num_frame_speech_samples += 1
            if num_frame_speech_samples >= ATTENUATION_SPEECH_SAMPLE_THRESHOLD:
                # Too many speech-like samples...
                frame_is_silent = False
                # Skip all remaining samples in this frame - we've already
                # decided it's not a silent frame - and move to the start of
                # the next frame. We do this by moving back to the start of
                # this frame and then moving forward one whole frame.
                sample_index = sample_index - \
                    (sample_index % FRAME_PERIOD_SAMPLES) + \
                    FRAME_PERIOD_SAMPLES
        if frame_is_silent:
            frame_sample_sum += sample

    # First pass is complete.
    #
    # We've accumulated the total sum of silence samples
    # (and have kept a copy of the start of each silent frame)
    # and know the total number of silent samples.
    #
    # Calculate the new `adc_zero` and replace all the samples in
    # every silent frame with this new estimate.

    if silence_sample_count:

        # A new ADC 'zero'?
        adc_zero = silence_sum // silence_sample_count
        # Now set each sample in each silent frame to this new value.
        # Remember that we collected all the silent frame indices
        # during our search for silence.
        for frame_index in range(num_silent_frames):
            sample_index = silent_frames[frame_index]
            for _ in range(FRAME_PERIOD_SAMPLES):
                s_buf[sample_index] = adc_zero
                sample_index += 1


# -----------------------------------------------------------------------------
def _capture_playback_loop():
    """The _main_ 'capture' and 'playback' loop.
    
    Before calling this method the PyBoard, the application variables
    and data structures must be prepared by first calling `_init()`.
    """

    global capture, initialised

    # Avoid any action if not initialised
    if not initialised:
        print('Not initialised')
        return

    # Enter the main loop.
    # If 'on-hold' we just wait.
    # Otherwise we move between capturing speech and playing it back.
    while True:

        # On hold?
        # If so, wait for user button.
        on_hold_notified = False
        while on_hold:

            # Toggle green LED
            grn_led.toggle()
            # Issue a one-time notification of the 'on-hold' state to stdout...
            if not on_hold_notified:
                print('On hold...')
                on_hold_notified = True
            # Pause
            utime.sleep_ms(USER_BUTTON_TOGGLE_MS)

        print('Listening...')

        # Start the capture process.
        # Switch green LED on to indicate that we're now listening.
        # `_capture_function()` will switch this off when speech
        # has been detected.
        _capture()

        # Capture is complete or it has stopped because the user's hit
        # the 'USER' button.
        #
        # If we find that we're now 'on-hold' we must wait for the current
        # capture to stop. Going 'on-hold' forces the `_capture_function()`
        # to end on its next iteration.
        if on_hold:
            while capture:
                utime.sleep_ms(CALLBACK_PAUSE_MS)

        # If not 'on hold' playback the speech buffer...
        if not on_hold:

            print('Heard ({} samples).'.format(eos_index))

            # The blue LED is set to indicate 'playback'.
            blu_led.on()

            # Copy speech detection buffer over the start of the speech buffer
            # and then attenuate...
            _copy_speech_detection_buffer()
            _attenuate_sb_silence()

            print('Playing...')

            # Play the captured speech and wait for it to
            # finish playing before stopping
            # (switching off the loudspeaker).
            _play()

            blu_led.off()

            # Try to dump the capture data (to file).
            # This only acts if enabled and there's an SD card.
            _dump_capture_info()


# -----------------------------------------------------------------------------
def _capture_function(timer):
    """The capture routine.
    
    Connected to a timer as a call-back (by the `_capture()` function)
    and called at the rate defined by SAMPLE_FREQUENCY_HZ.

    The 'timer' argument is not used.

    This function moves through three 'states'. Its initial state is
    'detect_speech'. Here it's monitoring the collected samples, writing
    them to a circular 'speech detection buffer', waiting for sufficient
    'high value' samples to occur in order to consider that speech has started.

    Once speech has been detected it moves to the 'recording' phase and writes
    new samples to the main 'speech buffer'. While 'recording' the capture
    function monitors the collected samples looking for a sufficiently quiet
    period of silence in order to detect the end of speech (or exhaust the
    speech buffer).
    
    Parameters
    ----------
    timer -- The timer, should you need it. We don't.
    """

    global adc_zero, eos, num_consec_post_speech_silence_frames
    global capture, detect_speech, eos_index
    global sdb_wr_offset, sb_wr_offset, ssc

    # Do nothing if not set to capture by the main loop (or ourselves).
    if not capture:
        return
    # Auto-stop if we now find ourselves 'on hold'.
    if on_hold:
        amb_led.off()
        detect_speech = True
        capture = False
        return

    # Lower the timing pin...
    capture_timing_pin.low()

    # Get a sample...
    new_sample = adc.read()
    if CAPTURE_BITS == 8:
        new_sample //= 16

    # Does the new sample represent speech?
    is_speech = False
    new_sample_delta = new_sample - adc_zero
    if new_sample_delta < 0:
        new_sample_delta *= -1

    # Are we listening (writing to detection buffer and listening for speech)
    # or have we detected speech and are now writing to the speech buffer?
    if detect_speech:

        if new_sample_delta >= SPEECH_THRESHOLD:
            is_speech = True

        # Update the current count of speech samples
        # in the detection buffer. We're writing to a circular buffer
        # so we also need to decrement (if we can) in order to age-out
        # previously detected speech.
        if is_speech:
            ssc += 1
        elif ssc > 0:
            ssc -= 1

        # Store the new sample
        sd_buf[sdb_wr_offset] = new_sample
        sdb_wr_offset = (sdb_wr_offset + 1) % SDB_SAMPLE_SIZE

        # Met the speech threshold?
        if ssc >= SPEECH_DETECTION_SAMPLE_THRESHOLD:
            # Yes - move out of speech detection mode
            detect_speech = False
            # Move LEDs from green to amber
            grn_led.off()
            amb_led.on()
            # Initialise the speech buffer offset
            # (we'll write to it on our next call)
            sb_wr_offset = SDB_SAMPLE_SIZE
            # Prepare for end-of-speech detection.
            # Reset the consecutive silence frame count
            # prior to starting our recording.
            eos = False
            ssc = 0
            num_consec_post_speech_silence_frames = 0

    else:

        # Speech detected.
        # We are now writing to the speech buffer
        # and do so until until speech has stopped or the
        # buffer is full.

        if new_sample_delta >= ATTENUATE_SPEECH_THRESHOLD:
            is_speech = True

        # Reset ssc at the start of each 'frame'.
        if sb_wr_offset > SDB_SAMPLE_SIZE and \
                sb_wr_offset % FRAME_PERIOD_SAMPLES == 0:

            # If the current speech sample count value is less then the
            # frame threshold for silence then the last frame was 'silent'
            # so we need to increment the consecutive silent frame count.
            if ssc < ATTENUATION_SPEECH_SAMPLE_THRESHOLD:

                # If we've now found the required number of consecutive
                # silent frames then we've found the 'end of speech' (eos).
                num_consec_post_speech_silence_frames += 1
                if num_consec_post_speech_silence_frames == \
                        EOS_CONSEC_SILENCE_FRAMES:

                    # Stopped speaking!
                    #
                    # Set the end-of-speech index to the sample at the
                    # start of the frame that's the first silent frame in our
                    # consecutive sequence. The `_playback_function()` stops
                    # when it gets to this value.
                    eos_index = sb_wr_offset - \
                                EOS_CONSEC_SILENCE_FRAMES * \
                                FRAME_PERIOD_SAMPLES
                    eos = True

            else:

                ssc = 0

        if not eos:

            # Speaking. Store the sample...
            s_buf[sb_wr_offset] = new_sample
            sb_wr_offset += 1

            # Count speech samples.
            # It's reset at the start of each frame so we don't need to
            # decrement as we do when we're listening.
            if is_speech:

                # Count
                ssc += 1

                # Too many speech samples in a frame?
                # If so, reset the consecutive frames count.
                # But only only once in each frame
                # (i.e. when ssc 'equals' the threshold)
                if ssc == ATTENUATION_SPEECH_SAMPLE_THRESHOLD:
                    num_consec_post_speech_silence_frames = 0

        if sb_wr_offset == SB_SAMPLE_SIZE:

            # No 'end of speech' but we've hit the end of the speech buffer.
            # Set eos index to the end of the buffer - we've run out of time.
            eos_index = SB_SAMPLE_SIZE
            eos = True

        # If speech has stopped then we should stop.
        # We do this by clearing the capture flag
        # (which will unblock the main loop and begin playback)
        if eos:

            # Auto-reset the `detecting speech` flag,
            # and the speech sample count.
            # so we're ready to capture again...
            detect_speech = True
            ssc = 0
            amb_led.off()

            # Switch ourselves off,
            # unblocking the main loop...
            capture = False

    # Raise the timing pin
    capture_timing_pin.high()


# -----------------------------------------------------------------------------
def _playback_function(timer):
    """The non-over-sampling playback routine.

    Connected to a timer as a call-back (by the `_play()` function)
    and called at the rate defined in PLAYBACK_FREQUENCY_HZ.

    The 'timer' argument is not used.

    This function is responsible for reading samples from the speech buffer
    and writing them to the DAC. It does this while `playback` is True and
    the sample it's reading is not at or past the _end of speech_ index.

    Parameters
    ----------
    timer -- The timer, should you need it. We don't.
    """

    global sb_rd_offset, playback, sub_sample

    # Do nothing if not playing
    if not playback:
        return

    # Lower the timing pin...
    playback_timing_pin.low()

    # We just write a value from the speech buffer to the DAC.
    dac.write(s_buf[sb_rd_offset])
    sb_rd_offset += 1

    # Stop when we've reached the `end of speech` marker.
    if sb_rd_offset >= eos_index:

        # Finished playing the speech buffer.
        #
        # Auto-reset the speech buffer read offset
        # in preparation for our next playback.
        sb_rd_offset = 0
        # And switch ourselves off
        playback = False

    # Raise the timing pin
    playback_timing_pin.high()


# -----------------------------------------------------------------------------
def _over_sample_playback_function(timer):
    """The over-sample playback routine.

    Connected to a timer as a call-back (by the `_play()` function)
    and called at 2x the capture rate.

    We over-sample the data and move through the data at the collection rate,
    For each sample we first write it to the DAC and the, on the next call
    we write an interpolated using the last and next sample.

    This allows us to move the DAC _whistle_ higher in frequency domain.
    Instead of an 8kHz _whistle_ (which is quite audible) the _whistle_
    moves to 16kHz and is less distracting. The interpolation allows us to
    reduce the quantisation error which would be more prominent if we simply
    repeated the samples.

    Parameters
    ----------
    timer -- The timer, should you need it. We don't.
    """

    global sb_rd_offset, playback, sub_sample

    # Do nothing if not playing
    if not playback:
        return

    # Lower the timing pin...
    playback_timing_pin.low()

    # Write the next value from the speech buffer to the DAC.
    # If we're up-scaling (playing at 2x capture rate) construct a new sample
    # using the average of the last sample, and the next.
    # This way we can reduce the DAC whistle by pushing it from 8kHz to 16kHz
    # for example.
    value = s_buf[sb_rd_offset]
    if sub_sample == 1 and sb_rd_offset < eos_index - 1:
        value += s_buf[sb_rd_offset + 1]
        value //= 2

    dac.write(value)

    sub_sample += 1
    # Move through the data every other call...
    if sub_sample == 2:
        sub_sample = 0
        sb_rd_offset += 1

        # Stop when we've reached the `end of speech` marker.
        if sb_rd_offset >= eos_index:

            # Finished playing the speech back
            #
            # Auto-reset the speech buffer read offset
            # in preparation for our next playback.
            sb_rd_offset = 0
            sub_sample = 0
            # And switch ourselves off
            playback = False

    # Raise the timing pin
    playback_timing_pin.high()


# -----------------------------------------------------------------------------
def echo():
    """Initialises and runs the main application.
    
    If initialisation fails the main loop does not run.
    Initialisation will fail if there is no audio skin.
    """

    if _init():
        _capture_playback_loop()


# -----------------------------------------------------------------------------
if __name__ == '__main__':
    # Just call the program entry point...
    echo()
