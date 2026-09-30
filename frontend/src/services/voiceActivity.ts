/**
 * Voice activity detection.
 *
 * Why this exists: silence detection used to average `getByteFrequencyData`
 * across all 128 bins and compare it to a hardcoded 0.015. A capture of the real
 * microphone (1262 frames x 80ms, mostly silence) showed that metric is
 * essentially untunable:
 *
 *   - The byte mean sits on a dB-domain average over ~111 noise bins, so its
 *     value is set by how many empty bins the spectrum has, not by signal
 *     level. It barely moves when the user speaks.
 *   - Measured on `avgAll`: the noise floor was 0.0140 and speech p90 was 0.0729
 *     -- a 0.41 dB gap, while a single byte step is 0.275 dB. There is no
 *     threshold that separates them, because there is nothing to separate.
 *   - 73.5% of the silent frames already read "loud", so the silence timer
 *     never armed and a quiet room recorded until the hard 180s cap.
 *
 * Time-domain RMS over the same capture separates cleanly:
 *
 *   noise floor  p50 0.0004   noise ceiling 0.0015
 *   speech                 peak 0.1499            (~250x, 48 dB)
 *
 * with an empty gap between 0.0015 and 0.0017. So the threshold is four times
 * the level of the last frame we called silence, rather than a hardcoded number.
 */

/**
 * Lower bound on the decision threshold, in RMS. Equal to the measured noise
 * ceiling, so a digitally silent mic -- whose tracked floor collapses to 0 --
 * still reads as silence rather than as speech.
 */
export const ABSOLUTE_NOISE_FLOOR = 0.0015;

/**
 * Speech must sit this far above the tracked floor. Four times the measured
 * floor is 0.0016, inside the gap between the noise ceiling (0.0015) and the
 * quietest frame that was actually speech (0.0017).
 */
export const NOISE_FLOOR_MULTIPLIER = 4;

export interface VadReading {
  /** RMS of the frame just read. */
  rms: number;
  /** Adaptive noise floor the decision was made against. */
  noiseFloor: number;
  /** The level a frame must exceed to count as speech. */
  threshold: number;
  speech: boolean;
}

export class VoiceActivityDetector {
  private floor = ABSOLUTE_NOISE_FLOOR;
  private timeDomain: Float32Array<ArrayBuffer> | null = null;

  /** Current adaptive noise floor. */
  get noiseFloor(): number {
    return this.floor;
  }

  /** Current decision threshold. */
  get threshold(): number {
    return Math.max(ABSOLUTE_NOISE_FLOOR, this.floor * NOISE_FLOOR_MULTIPLIER);
  }

  /**
   * Read one frame from an analyser and classify it. Returns null when there is
   * no analyser, so callers can treat "no signal yet" as "not speech" without a
   * null check on every field.
   */
  read(analyser: AnalyserNode | null): VadReading | null {
    if (!analyser) return null;
    if (!this.timeDomain || this.timeDomain.length !== analyser.fftSize) {
      this.timeDomain = new Float32Array(analyser.fftSize);
    }
    const td = this.timeDomain;
    analyser.getFloatTimeDomainData(td);
    let sumSquares = 0;
    for (let i = 0; i < td.length; i++) sumSquares += td[i] * td[i];
    return this.push(Math.sqrt(sumSquares / td.length));
  }

  /** Classify a pre-computed RMS. Exposed so the decision is testable. */
  push(rms: number): VadReading {
    // Decided against the previous frame's floor, so a frame never helps
    // justify its own verdict.
    const speech = rms > this.threshold;

    // A frame we just called silence *is* a noise sample, so the estimate has
    // no lag and the room's real level is always the one in force. Taking the
    // floor from speech frames instead would let a few seconds of talking drag
    // it up to the voice level and cut the speaker off mid-sentence.
    if (!speech) this.floor = rms;

    return { rms, noiseFloor: this.floor, threshold: this.threshold, speech };
  }

  /** Forget the calibration. Call when a new recording starts. */
  reset(): void {
    this.floor = ABSOLUTE_NOISE_FLOOR;
  }
}

// Known limitation, from the frozen-floor rule above: a room whose noise never
// dips below NOISE_FLOOR_MULTIPLIER x the floor calibrated in a quieter room is
// read as continuous speech, so the silence timer cannot arm. The fix is a
// handful of lines once it has been seen with a real capture -- a measured
// re-seed point, not another guessed constant.
