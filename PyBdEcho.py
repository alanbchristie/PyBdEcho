"""PyBdEcho.py

https://github.com/alanbchristie/PyBdEcho

Refer to project's LICENCE.txt for licence restrictions.

The PyBoard 'Echo' audio capture/playback demonstration code.
Presented at EuroPython, Rimini, July 2017. This application
requires MicroPython (tested with v 1.9.1), PyBoard v1.1 and the
AMP Audio skin v1.0.

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

# The sampling frequency.
SAMPLE_FREQUENCY_HZ = 8000

# The playback frequency.
PLAYBACK_FREQUENCY_HZ = 12000

# Capture resolution bits (8 or 12).
CAPTURE_BITS = 8

# Size of the Speech Detection Buffer (SDB) (milliseconds).
# This is the circular buffer used by the `_capture_function()`
# while it's listening for speech.
SDB_SIZE_MS = 500

# Size of the Speech Buffer (SB) (seconds).
# Written to until full by the `_capture_function()` once
# speech has been detected. It has to be larger than the
# speech detection buffer, which is copied over the start
# of this buffer prior to playback.
# At 8kHz & 12-bit resolution I find I have enough memory for 3 seconds.
# At 8kHz & 8-bit resolution I find I have enough memory for 8 seconds.
SB_SIZE_S = 4

# Loudspeaker volume.
# 0 (off) to 127 (maximum).
LS_VOLUME = 127

# Speech threshold - the absolute difference between the silence estimate
# and a sample for it to be considered speech. There's a lot of noise
# on my board so expect +/- 156 at 8kHz, 12-bit (or 10 at 8-bit).
# Currently the estimate is not modified as recordings are made
# (although it could be adapted during attenuation).
# This value is 2 x standard deviation of typical noise levels.
if CAPTURE_BITS == 8:
    SPEECH_THRESHOLD = 10
else:
    SPEECH_THRESHOLD = 156

# Speech detection volume (percent).
# The proportion of the number of speech samples observed during the
# speech detection phase that are required to trigger a recording.
# Measured as a percentage size of the speech-detection buffer and
# should probably be greater than 5%.
DETECTION_SAMPLES_PCENT = 10

# Estimate of the sample value for silence
# (in an ideal world this would be 2048 for 12-bit data and 127 for 8-bit).
# This is just a default seed for the `adc_zero` value, which is adjusted
# during attenuation, a process that occurs immediately prior to playback.
# My board seems to settle around a value of 1893 at 12-bits.
# Your starting value might be different depending on amp-skin resistor
# tolerances (see R11 & R13).
# `adc_zero` is not modified if attenuation is disabled.
if CAPTURE_BITS == 8:
    SILENCE = 127
else:
    SILENCE = 1893

# The 'toggle-rate' of the green LED when 'On Hold'.
# This is the period between 'on' and 'off' states of the LED when
# recording has been put 'on hold' with the USER button.
USER_BUTTON_TOGGLE_MS = 750

# Capture status poll period.
# When a capture is in progress, this is the length of time the 'main loop'
# sleeps between consecutive tests of the 'capture' flag. When the flag clears
# a recording can be played.
CAPTURE_POLL_MS = 250

# -----------------
# Derived constants
# -----------------
# Values derived from the above constants.
# Don't edit these, just edit the corresponding constant(s).

# Speech detection buffer size (in samples)
SDB_SAMPLE_SIZE = SDB_SIZE_MS * SAMPLE_FREQUENCY_HZ // 1000

# The size of the main speech buffer (in samples).
SB_SAMPLE_SIZE = SB_SIZE_S * SAMPLE_FREQUENCY_HZ

# Speech buffer duration (milliseconds) - the length of time
# the speech buffer represents when played at the playback frequency.
# Playback via the DAC is non-blocking so once we begin playback
# we need to 'sleep' for this period before we can do anything else.
SB_DURATION_MS = 1000 * SB_SAMPLE_SIZE // PLAYBACK_FREQUENCY_HZ

# Absolute number of speech samples required to occupy the
# speech detection buffer for the buffer to be considered to
# contain the start of speech.
DETECTION_SAMPLE_THRESHOLD = SDB_SAMPLE_SIZE * DETECTION_SAMPLES_PCENT // 100

# ---------
# Variables
# ---------
# All the run-time application variables (globals, sorry)...

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
# `_capture_function()` when speech has been detected. Samples storage
# moves to the speech buffer on the next invocation of the function.
# It is returned to True at the end of each recording
# so that the next recording starts with a fresh attempt to detect speech.
detect_speech = True

# Speech detection speech sample count.
# The number of samples in the speech detection buffer considered speech.
# It is incremented for each speech sample collected and decremented (to zero)
# for each non-speech sample. When it meets the DETECTION_SAMPLE_THRESHOLD
# then someone has started talking and the `_capture_function()` moves to
# writing to the speech buffer. It is reset when the `_capture_function()`
# moves to writing to the speech buffer.
spc = 0

# The ADC value that represents zero.
# The value is adjusted during the attenuation phase,
# which run (if not disabled) after recordings have been made.
adc_zero = SILENCE

# The current speech-detection buffer write offset. A circular offset
# used by `_capture_function()`. Updated from within `_capture_function()`.
sdb_wr_offset = 0

# When we've detected speech we switch from writing to the speech detection
# buffer to writing to the main speech buffer. And, in this version, we write
# to the speech buffer until it's full. The offset accommodates a copy of the
# speech detection buffer, which is copied in prior to playback.
# The `sb_wr_offset` is updated from within `_capture_function()`
# and reset once speech is detected.
sb_wr_offset = SDB_SAMPLE_SIZE

# The initialisation state.
# Set after `_init()` has completed successfully.
initialised = False

# ---------------
# PyBoard objects
# ---------------
# The numerous PyBoard objects, timers, LEDs etc. Objects that need
# configuration after construction are configured inside `_init()`.

# The capture timer. This is used to invoke our `_capture_function()`
# at the designated SAMPLE_FREQUENCY_HZ.
# Configured in `_init()`
capture_timer = pyb.Timer(14)

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

# -----------------
# Performance timer
# -----------------
# We would like a performance timer to measure execution time of the
# time-sensitive `_capture_function()` in the hope that it will execute
# within the sampling period (i.e. 125uS at 8kHz and 167uS at 6kHz).
# As as long as we have a timer with enough bits to easily count to 167
# we should be OK.
#
# Timer 8 is a 16-bit timer driven by a 168MHz clock.
# To count microseconds we need to divide the clock by 168
# using a `prescaler` of 167.
#
# To use this timer, it’s best to first reset it to 0.
# as it will wrap after approximately 65mS.
#
# >>> micros_timer.counter(0)
#     ... do some stuff ...
# >>> elapsed = micros.counter()
micros_timer = pyb.Timer(8,                 # Timer 8 (168MHz)
                         prescaler=167,     # Divide clock by 168
                         period=0xffff)     # Use all 16-bits (up to 65mS?)

# Variables used to record the min/max time spent in the capture function.
# These are microsecond durations for the speech detection (sd) and collection
# (sc) phases.
sd_min_duration_us = 1000000    # Something much bigger than 1/SAMPLE_RATE
sd_max_duration_us = 0
sc_min_duration_us = 1000000    # Something much bigger than 1/SAMPLE_RATE
sc_max_duration_us = 0

# ------------------
# ADC sample buffers
# ------------------
# 16-bit (unsigned) buffers to store captured audio samples.
# The size of the arrays will be set by appending zeros during `_init()`.
# We need one for the circular 'speech detection' buffer.
# We need one to record the 'speech' once speech has been detected.

if CAPTURE_BITS == 8:
    sd_buf = array('B')
    s_buf = array('B')
else:
    sd_buf = array('H')
    s_buf = array('H')

# ---------------------------------
# Silence attenuation configuration
# ---------------------------------
# Attenuation properties.

# Enabled?
ATTENUATE_SILENCE = True
# A 'frame' for the purpose of identifying areas of the speech buffer
# that contain only 'silence' samples. Frames that contain only silence
# samples are attenuated.
FRAME_PERIOD_MILLIS = 100
# Speech threshold during attenuation - the absolute difference between the
# silence estimate and a sample for it to be considered speech during
# an attenuation frame. This is normally higher than the SPEECH_THRESHOLD
# so we only attenuate if we're really sure it's not speech.
# For 12-bit 6kHz recordings I use a value of around 800.
# for 12-bit 8kHz recordings I use a value of around 500.
if CAPTURE_BITS == 8:
    ATTENUATE_SPEECH_THRESHOLD = 32
else:
    ATTENUATE_SPEECH_THRESHOLD = 500
# The percentage of samples that need to be speech in a speech-buffer
# frame to prevent it from being attenuated. This is somewhat lower
# than the corresponding speech detection threshold so only a few samples
# need to represent speech to prevent the fame from being attenuated.
ATTENUATION_SAMPLES_PCENT = 1
# The number of samples in a frame...
FRAME_PERIOD_SAMPLES = SAMPLE_FREQUENCY_HZ * FRAME_PERIOD_MILLIS // 1000
# The number of frames in the speech buffer (will/must be whole)
SB_FRAME_COUNT = SB_SAMPLE_SIZE // FRAME_PERIOD_SAMPLES
# Frame period samples required to be speech
# before the frame is considered part of speech.
ATTENUATION_THRESHOLD = FRAME_PERIOD_SAMPLES * ATTENUATION_SAMPLES_PCENT // 100
# An array to hold a list of the first sample index of silent frames.
# Used during a 2nd-pass in attenuation to quickly attenuate silent
# frames found in the 1st-pass.
silent_frames = array('I')

# --------------------------------
# Configuration of diagnostic dump
# --------------------------------
# Dump collections to a connected SD card.

# Set to write collect data to an attached SD card.
# Writing to the SD card will only take place
# if it looks like there's an SD card present.
# Incidentally ... You will need to hard-reset the card
# before you can see any written files.
DUMP_TO_SD_CARD = False
# Maximum number of capture files to maintain.
# The files are used on a round-robin basis by writing
# to capture file 1, then capture file 2, etc.
DUMP_FILE_LIMIT = 50
# The next capture file number...
dump_file_num = 1

# -------------------
# Hardware timing pin
# -------------------
# The pin used for hardware-based timing.
# This pin voltage is lowered on entry to the capture function
# and raised on exit. Attach an oscilloscope to this pin to measure
# the `_collect_function()` duration.
timing_pin = pyb.Pin('Y1', pyb.Pin.OUT_PP)


# -----------------------------------------------------------------------------
def _init():
    """Initialise the application data and hardware.

    If already initialised this function does nothing.
     
    Returns 'True' if successfully initialised.
    """

    global adc, dac, capture, sd_buf, s_buf, capture_timer, initialised
    global DETECTION_SAMPLE_THRESHOLD, silent_frames, timing_pin
    global on_hold

    # Do nothing if already initialised
    if initialised:
        return

    print('Initialising...')

    # Sanity-check on capture resolution
    if CAPTURE_BITS not in [8, 12]:
        print('CAPTURE_BITS must be 8 or 12, not {}'.format(CAPTURE_BITS))
        return

    grn_led.off()   # List when listening (flashing when 'on hold')
    amb_led.off()   # Lit when writing to the speech buffer
    blu_led.off()   # Lit when playing back the speech buffer
    red_led.off()   # Lit when writing to SD card/flash

    # Initialise the capture hardware timing pin (set it to 'high').
    timing_pin.high()

    # Prevent the capture function from doing anything.
    # It gets called at the capture sample rate but does nothing
    # if `capture` is false.
    capture = False

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

    # Set loud-speaker volume.
    # This may fail if there are problems with the board.
    if not _set_volume(LS_VOLUME):
        print('set_volume({}) failed.'
              ' Is the Audio Skin attached?'.format(LS_VOLUME))
        return

    # Create a timer and attach our collect function.
    # The function will do nothing while 'capture' is False.
    capture_timer.init(freq=SAMPLE_FREQUENCY_HZ)
    capture_timer.callback(_capture_function)

    # Stop the loudspeaker (just be safe)
    _stop()

    # Attach a service function that will handle the USER switch being hit.
    # The supplied function toggles the 'on hold' flag.
    sw.callback(_user_switch_callback)

    initialised = True

    print('Initialised.')

    return True


# -----------------------------------------------------------------------------
def _user_switch_callback():
    """Called in response to the USER switch being depressed.
    When 'on-hold' (not listening) the green LED flashes.
    When listening the green LED is solid.
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
    if '/sd' not in sys.path:
        print('DUMP_TO_FILE is set but there is no SD card.')
        return

    # Indicate we're writing to the SD card...
    red_led.on()

    # Construct the intended dump file name...
    dump_file = '{}/PyBdEcho.{}.txt'.format('/sd', dump_file_num)
    # What's the next file number? (1..N)
    dump_file_num += 1
    if dump_file_num > DUMP_FILE_LIMIT:
        dump_file_num = 1

    # Open, write, close...

    print('Dumping to {}...'.format(dump_file))
    fp = open(dump_file, 'w')

    fp.write("sdb->\n")
    for i in range(SDB_SAMPLE_SIZE):
        value = sd_buf[i]
        fp.write("{}\n".format(value))

    fp.write("sb->\n")
    for i in range(SB_SAMPLE_SIZE):
        value = s_buf[i]
        fp.write("{}\n".format(value))

    fp.write("adc_zero {}\n".format(adc_zero))
    fp.write("sd {}-{}uS\n".format(sd_min_duration_us, sd_max_duration_us))
    fp.write("sc {}-{}uS\n".format(sc_min_duration_us, sc_max_duration_us))
    fp.write("sb_wr_offset {}\n".format(sb_wr_offset))
    fp.write("sdb_wr_offset {}\n".format(sdb_wr_offset))

    fp.close()

    print('Dumped.')

    # Indicate end of file operations...
    red_led.off()


# -----------------------------------------------------------------------------
def _set_volume(volume):
    """Sets the DAC (loudspeaker) volume. Range is 0 (off) to 127.
    
    Returns False on error - usually an indication of a missing audio skin.
    If this fails the `_init()` should also fail, preventing the main
    application from running.
    
    Parameters
    ----------
    volume -- The volume 0..127 (int)
    
    Returns False on failure
    """

    try:
        pyb.I2C(1, pyb.I2C.MASTER).mem_write(volume, 46, 0)
    except OSError as e:
        print('ERROR: OSError {}'.format(e))
        return False

    # OK if we get here
    return True


# -----------------------------------------------------------------------------
def _play():
    """Plays the speech buffer (sb) to the loudspeaker (DAC)
    at the playback frequency. The speech buffer is written to the DAC
    in a non-blocking fashion, so the caller has to wait for sufficient
    time to elapse before being sure the audio has finished playing.
    
    The caller must ensure that the speech-detection buffer
    has been copied into the spare space at the start of the speech
    buffer.
    """

    dac.write_timed(s_buf,
                    pyb.Timer(7, freq=PLAYBACK_FREQUENCY_HZ),
                    mode=pyb.DAC.NORMAL)


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
    # is at index `sdb_wr_offset - 1`. We unroll the speech detection
    # over the start of the speech buffer, backwards, starting with the
    # last written speech detection value. Here, `to_index` moves backwards
    # to the start of the speech buffer and `from_index` works back
    # through the speech detection buffer (in a reverse circular fashion).
    from_index = sdb_wr_offset
    for to_index in range(SDB_SAMPLE_SIZE - 1, -1, -1):
        from_index -= 1
        if from_index < 0:
            from_index = SDB_SAMPLE_SIZE - 1
        s_buf[to_index] = sd_buf[from_index]


# -----------------------------------------------------------------------------
def _attenuate_sb_silence():
    """Attempts to attenuate areas of the speech buffer that are silent.
    
    This function does a number of things. Firstly, it calculates a new ADC
    silence level from the average value found across all 'frames' that are
    thought to be represent silence. It then makes a second pass passes trough
    the speech buffer setting all the silent frames to the new ADC average.
    
    This method can be disabled by setting ATTENUATE_SILENCE to False.
    """

    global adc_zero, silent_frames

    # Do nothing if disabled
    if not ATTENUATE_SILENCE:
        return

    # Search each 'frame' from the start of the speech buffer.
    # If the frame is silent then accumulate all the samples in it.
    # At the end we calculate a new ADC zero and set the samples
    # in each silent frame we found to the new zero.

    print("Attenuating silence...")

    silence_sum = 0                 # The sum of all samples in silent frames
    silence_sample_count = 0        # Total number of silent samples
    frame_sample_sum = 0            # Sum of samples in the current frame
    num_frame_speech_samples = 0    # Number of speech samples in current frame
    frame_is_silent = True          # True if the current frame is silent
    silent_frame_index = 0          # Next index into silent_frames array
    num_silent_frames = 0           # Number of silent frames

    sample_index = 0
    # Run over the whole speech buffer (plus one sample).
    # The last sample lets us handle the last possible frame.
    while sample_index < SB_SAMPLE_SIZE + 1:

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
            if sample_index == SB_SAMPLE_SIZE:
                break
            # Otherwise - we're starting a frame.
            # Reset the frame sample sum
            # and assume it is going to be silent...
            num_frame_speech_samples = 0
            frame_sample_sum = 0
            frame_is_silent = True

        # Get the next sample from the frame.
        # Is it a silent sample? (compared to the existing `adc_zero`).
        # Once this function completes we may have a new `adc_zero`.
        sample = s_buf[sample_index]
        sample_index += 1
        delta = sample - adc_zero
        if delta < 0:
            delta *= -1
        if delta >= ATTENUATE_SPEECH_THRESHOLD:
            # Any speech-sized sample might prevent this frame
            # from being considered silent. Once we reach the
            # ATTENUATION_THRESHOLD in a frame then it is not a silent frame.
            num_frame_speech_samples += 1
            if num_frame_speech_samples >= ATTENUATION_THRESHOLD:
                # Too many speech-like samples...
                frame_is_silent = False
                # Skip to the start of the next frame...
                # By moving back to the start of this frame and moving
                # forward one whole frame.
                sample_index = sample_index - \
                    (sample_index % FRAME_PERIOD_SAMPLES) + \
                    FRAME_PERIOD_SAMPLES
        if frame_is_silent:
            frame_sample_sum += sample

    # We've accumulated the total sum of silence samples
    # (and have kept a copy of the start of each silent frame)
    # and know the total number of silent samples.
    #
    # Calculate the new `adc_zero`
    # and replace all the samples in
    # every silent frame with this new average.
    if silence_sample_count:
        adc_zero = silence_sum // silence_sample_count
        print('(New adc_zero={})'.format(adc_zero))
        # Now set each silent frame to this new value.
        # We collected all the silent frame offsets
        # during our search for silence.
        for frame_index in range(num_silent_frames):
            sample_index = silent_frames[frame_index]
            for _ in range(FRAME_PERIOD_SAMPLES):
                s_buf[sample_index] = adc_zero
                sample_index += 1
    else:
        print('(No silence)')

    print("Attenuated.")


# -----------------------------------------------------------------------------
def _capture_playback_loop():
    """The _main_ 'capture' and 'playback' loop.
    
    Before calling this method the PyBoard, the application variables
    and data structures must be prepared by first calling `_init()`.
    """

    global capture, initialised, adc
    global grn_led

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

        # Switch green LED on to indicate that we're now listening.
        # `_capture_function()` will switch this off
        # when speech has been detected.
        grn_led.on()

        # Set the capture flag.
        # This causes the `_capture_function()` to 'do work'.
        capture = True

        # Wait, in a CPU-friendly way,
        # for the capture process to complete.
        while capture and not on_hold:
            utime.sleep_ms(CAPTURE_POLL_MS)

        # Capture is complete or it has stopped
        # because the user's hit the 'USER' button.
        # If we find that we're now 'on-hold' wait for the capture to stop.
        # (going 'on-hold' forces the `_capture_function()` to end on its
        # next iteration).
        if on_hold:
            while capture:
                utime.sleep_ms(CAPTURE_POLL_MS)

        # If not 'on hold' playback the speech buffer...
        if not on_hold:
            print('Heard.')

            # The blue LED is set to indicate 'playback'.
            blu_led.on()

            # Copy speech detection buffer
            # over the start of the speech buffer
            # and then attenuate...
            _copy_speech_detection_buffer()
            _attenuate_sb_silence()

            print('Playing...')

            # Play the captured speech and pause long enough for it to
            # finish playing before stopping (and resetting the DAC).
            _play()
            utime.sleep_ms(SB_DURATION_MS)
            _stop()

            print('Played.')

            blu_led.off()

            # Dump the capture data (to file).
            # This only acts if enabled and there's an SD card.
            _dump_capture_info()


# -----------------------------------------------------------------------------
def _capture_function(timer):
    """The capture routine.
    
    Connected to a timer as a call-back and called at the rate defined
    in SAMPLE_FREQUENCY_HZ. The 'timer' argument is not used.
    
    Parameters
    ----------
    timer -- The timer, should you need it. We don't.
    """

    global adc_zero
    global capture, adc, detect_speech
    global sdb_wr_offset, sb_wr_offset, spc
    global grn_led, amb_led, timing_pin
    global sd_max_duration_us, sd_min_duration_us
    global sc_max_duration_us, sc_min_duration_us

    # Do nothing if not set to capture by teh main loop.
    # Also, auto-stop if we find ourselves 'on hold'/
    if not capture:
        return
    if on_hold:
        amb_led.off()
        detect_speech = True
        capture = False
        return

    # Lower the timing pin
    # and reset the precision timer...
    timing_pin.low()
    micros_timer.counter(0)

    # Get a sample...
    new_sample = adc.read()
    if CAPTURE_BITS == 8:
        new_sample //= 16

    # Does the new sample represent speech?
    is_speech = False
    new_sample_delta = new_sample - adc_zero
    if new_sample_delta < 0:
        new_sample_delta *= -1
    if new_sample_delta >= SPEECH_THRESHOLD:
        is_speech = True

    # Are we listening (writing to detection buffer and listening for speech)
    # or have we detected speech and are now writing to the speech buffer?
    if detect_speech:

        # Update the current count of speech samples
        # in the detection buffer.
        if is_speech:
            spc += 1
        elif spc > 0:
            spc -= 1

        # Store the new sample
        sd_buf[sdb_wr_offset] = new_sample
        sdb_wr_offset = (sdb_wr_offset + 1) % SDB_SAMPLE_SIZE

        # Met the speech threshold?
        if spc >= DETECTION_SAMPLE_THRESHOLD:
            # Yes - move out of speech detection mode
            detect_speech = False
            # Move LEDs from green to amber
            grn_led.off()
            amb_led.on()
            # Reset speech buffer offset
            # and the detector speech sample count
            sb_wr_offset = SDB_SAMPLE_SIZE
            spc = 0

        # Update min and max execution times
        # for the speech detection (sd) stage...
        elapsed_micros = micros_timer.counter()
        if elapsed_micros > sd_max_duration_us:
            sd_max_duration_us = elapsed_micros
        if elapsed_micros < sd_min_duration_us:
            sd_min_duration_us = elapsed_micros

    else:

        # Speech detected.
        # We are now writing to the speech buffer
        # and do so until until it is full.

        s_buf[sb_wr_offset] = new_sample
        sb_wr_offset += 1

        # If we've filled the speech buffer
        # clear the capture flag
        # (which will unblock the main loop and begin playback)

        if sb_wr_offset == SB_SAMPLE_SIZE:
            # Stopped capture...
            amb_led.off()

            # Back to detecting speech if we told to capture again...
            detect_speech = True

            # Switch ourselves off,
            # unblocking the main loop...
            capture = False

        # Record min and max execution times
        # for the speech collection (sc) stage...
        elapsed_micros = micros_timer.counter()
        if elapsed_micros > sc_max_duration_us:
            sc_max_duration_us = elapsed_micros
        if elapsed_micros < sc_min_duration_us:
            sc_min_duration_us = elapsed_micros

    # Raise the timing pin
    timing_pin.high()


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
