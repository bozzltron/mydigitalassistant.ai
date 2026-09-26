import { describe, it, expect } from 'vitest';
import { VoiceActivityDetector, ABSOLUTE_NOISE_FLOOR } from './voiceActivity';

// Levels replayed from a 1262-frame x 80ms capture of the real microphone
// (~101s, a few seconds of speech, the rest silence). These are the measured
// distributions, not invented numbers.
const MEASURED_QUIET = [0.0003, 0.0004, 0.0004, 0.0006, 0.0015];
// The same frames as a running stream, in the order the mic produced them: the
// ceiling frame (0.0015) is the rare end of the range, not the resting level.
const MEASURED_QUIET_STREAM = [
  0.0004, 0.0003, 0.0006, 0.0004, 0.0015, 0.0004, 0.0004, 0.0003, 0.0006, 0.0004,
];
const MEASURED_SPEECH = [0.0017, 0.0017, 0.02, 0.1499];

describe('VoiceActivityDetector', () => {
  it('reads the measured noise floor as silence', () => {
    // Regression: on the old byte-mean metric 73.5% of these silent frames
    // scored above the hardcoded 0.015 threshold, so the silence timer never
    // armed and a quiet room recorded until the 180s cap.
    const vad = new VoiceActivityDetector();
    const readings = MEASURED_QUIET.map((rms) => vad.push(rms));

    expect(readings.every((r) => !r.speech)).toBe(true);
  });

  it('reads the measured speech levels as speech', () => {
    const vad = new VoiceActivityDetector();
    MEASURED_QUIET_STREAM.forEach((rms) => vad.push(rms));

    const readings = MEASURED_SPEECH.map((rms) => vad.push(rms));

    expect(readings.every((r) => r.speech)).toBe(true);
  });

  it('detects speech in the very first frame of a recording', () => {
    // A user who taps the mic and talks immediately has no silent frames to
    // calibrate against first. The initial floor must still be low enough to
    // hear them, or the opening words of every quick reply are lost.
    const vad = new VoiceActivityDetector();

    expect(vad.push(0.02).speech).toBe(true);
  });

  it('places the threshold in the measured gap between noise and speech', () => {
    const vad = new VoiceActivityDetector();
    MEASURED_QUIET_STREAM.forEach((rms) => vad.push(rms));

    const threshold = vad.threshold;

    // Above the loudest silent frame the mic ever produced...
    expect(threshold).toBeGreaterThanOrEqual(0.0015);
    // ...and below the quietest frame that was actually speech.
    expect(threshold).toBeLessThan(0.0017);
  });

  it('treats a dead-silent input as silence rather than speech', () => {
    const vad = new VoiceActivityDetector();

    // Without an absolute floor a zero tracked floor gives a zero threshold,
    // so every frame of digital silence would read as speech.
    for (let i = 0; i < 20; i++) {
      expect(vad.threshold).toBeGreaterThanOrEqual(ABSOLUTE_NOISE_FLOOR);
      expect(vad.push(0).speech).toBe(false);
    }
  });

  it('keeps the floor put across a long utterance', () => {
    // The failure this guards: if the floor ever tracked speech, a few seconds
    // of talking would drag it up to the voice level and the rest of the
    // sentence would be cut off as silence.
    const vad = new VoiceActivityDetector();
    for (let i = 0; i < 30; i++) vad.push(0.0004);
    const floorWhileQuiet = vad.noiseFloor;

    // 24 seconds of speech with an inter-word gap every 400ms. No single frame
    // of the utterance may read as silence, or the silence timer arms and stops
    // the recording mid-sentence.
    for (let i = 0; i < 300; i++) {
      for (let f = 0; f < 5; f++) expect(vad.push(0.05).speech).toBe(true);
      vad.push(0.0004);
    }

    expect(vad.noiseFloor).toBeCloseTo(floorWhileQuiet, 10);
  });

  it('follows the room down again on the first silent frame', () => {
    // After a pause the floor must be back at the real room level immediately,
    // so a following quiet word is still heard.
    const vad = new VoiceActivityDetector();
    for (let i = 0; i < 30; i++) vad.push(0.0004);
    for (let i = 0; i < 40; i++) vad.push(0.05);

    vad.push(0.0004);

    expect(vad.noiseFloor).toBe(0.0004);
    expect(vad.push(0.0017).speech).toBe(true);
  });

  it('forgets the calibration on reset so turns do not contaminate each other', () => {
    const vad = new VoiceActivityDetector();
    for (let i = 0; i < 30; i++) vad.push(0.002);
    expect(vad.noiseFloor).not.toBe(ABSOLUTE_NOISE_FLOOR);

    vad.reset();

    expect(vad.noiseFloor).toBe(ABSOLUTE_NOISE_FLOOR);
  });

  it('reports no reading when there is no analyser', () => {
    const vad = new VoiceActivityDetector();
    expect(vad.read(null)).toBeNull();
  });

  it('reads RMS from the analyser, resizing the buffer if the fftSize changed', () => {
    const vad = new VoiceActivityDetector();
    // Constant-amplitude signal: RMS is the amplitude itself.
    const makeAnalyser = (size: number, value: number) =>
      ({
        fftSize: size,
        getFloatTimeDomainData: (arr: Float32Array) => arr.fill(value),
      }) as unknown as AnalyserNode;

    expect(vad.read(makeAnalyser(256, 0.05))!.rms).toBeCloseTo(0.05, 6);
    expect(vad.read(makeAnalyser(1024, 0.0004))!.rms).toBeCloseTo(0.0004, 6);
    // Reading the resized buffer at room level must not look like speech.
    expect(vad.read(makeAnalyser(1024, 0.0004))!.speech).toBe(false);
  });
});
