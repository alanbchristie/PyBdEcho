# PyBdEcho
An audio demonstration using [MicroPython], the [PyBoard] and its [AMP Skin].

For a discussion of the application and its development refer to my
EuroPython [presentation] in Rimini, July 2017.

## Construction
You need good soldering skills to build the [AMP skin].
Once built you'll be able to access the speaker
using DAC port `1` and the microphone's ADC on `X22`.

## Presentation
The presentation slides (exported as a PDF document) can be found in the
`presentation` directory.

### Issue 2
Contains the following minor corrections to the original presentation slides.

-   Typo in first bullet-point of slide `15`. `4096` should be `4095`
-   Correction of the block diagram in slide `23`. The green LED was shown
    as the `Playback` LED when it should have been the blue LED

---

[MicroPython]:  http://micropython.org
[PyBoard]:      https://micropython.org/store/#/store
[AMP Skin]:     https://micropython.org/store/#/products/AMPv1_0
[Presentation]: https://ep2017.europython.eu/conference/talks/building-a-real-time-embedded-audio-sampling-application-with-micropython
