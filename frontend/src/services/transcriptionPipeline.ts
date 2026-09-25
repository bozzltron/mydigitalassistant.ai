import { enqueue, isProcessing, setProcessing } from '../state/messageQueue';

/**
 * TranscriptionPipeline - Decouples voice transcription from chat processing.
 * Runs independently, outputs transcribed text to MessageQueue.
 */
export class TranscriptionPipeline {
  private isRunning = false;
  private currentMimeType: string | null = null;
  private mediaRecorder: MediaRecorder | null = null;
  private audioChunks: Blob[] = [];
  private mediaStream: MediaStream | null = null;
  private audioContext: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private silenceTimeout: number | null = null;
  private recordingTimeoutId: number | null = null;
  private recordingStartTime = 0;
  private loudFrameCount = 0;
  private silenceAfterLoud = false;
  private monitorIntervalId: number | null = null;

  // Configuration
  private readonly SILENCE_DURATION = 2000;
  private readonly MIN_RECORDING_MS = 500;
  private readonly MIN_AUDIO_LEVEL = 0.015;
  private readonly MIN_AUDIO_FRAMES = 3;
  private readonly MAX_RECORDING_MS = 180000;
  private readonly MONITOR_INTERVAL_MS = 80;

  /**
   * Start the transcription pipeline for the active conversation.
   * Begins continuous listening with silence detection.
   */
  async start(): Promise<void> {
    if (this.isRunning) return;
    this.isRunning = true;
    await this.startRecording();
  }

  /**
   * Stop the transcription pipeline.
   * Cleans up all resources.
   */
  async stop(): Promise<void> {
    this.isRunning = false;
    this.stopRecording();
    this.cleanup();
  }

  /**
   * Check if pipeline is currently running.
   */
  get running(): boolean {
    return this.isRunning;
  }

  // ==========================================================================
  // Recording Logic (adapted from useVoiceRecording)
  // ==========================================================================

  private async startRecording(): Promise<void> {
    if (!this.isRunning) return;

    try {
      // Close existing audio context
      if (this.audioContext) {
        await this.audioContext.close().catch(() => {});
      }

      // Get user media
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      this.mediaStream = stream;

      // Setup audio context for level monitoring
      const AudioContextCtor = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      this.audioContext = new AudioContextCtor();
      this.analyser = this.audioContext.createAnalyser();
      this.analyser.fftSize = 256;

      const source = this.audioContext.createMediaStreamSource(stream);
      source.connect(this.analyser);

      // Determine mime type
      this.currentMimeType =
        MediaRecorder.isTypeSupported('audio/ogg') ? 'audio/ogg' :
        MediaRecorder.isTypeSupported('audio/wav') ? 'audio/wav' :
        MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' :
        MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4' :
        null;

      if (!this.currentMimeType) {
        console.error('Audio recording not supported in this browser');
        this.scheduleRestart('Recording not supported');
        return;
      }

      // Create MediaRecorder
      this.mediaRecorder = new MediaRecorder(this.mediaStream, { mimeType: this.currentMimeType });
      this.audioChunks = [];

      this.mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          this.audioChunks.push(e.data);
        }
      };

      this.mediaRecorder.onstop = async () => {
        await this.handleRecordingStop();
      };

      this.mediaRecorder.onerror = (e) => {
        console.error('MediaRecorder error:', e);
        if (this.isRunning) {
          this.scheduleRestart('Recording error');
        }
      };

      this.mediaRecorder.start();
      this.recordingStartTime = Date.now();
      this.loudFrameCount = 0;
      this.silenceAfterLoud = false;

      this.startAudioMonitor();

      // Max recording timeout
      this.recordingTimeoutId = window.setTimeout(() => {
        if (this.mediaRecorder?.state === 'recording') {
          this.mediaRecorder.stop();
        }
      }, this.MAX_RECORDING_MS);

    } catch (e) {
      console.warn('Failed to start recording:', e);
      if (this.isRunning) {
        this.scheduleRestart('Failed to start recording');
      }
    }
  }

  private stopRecording(): void {
    if (this.mediaRecorder?.state === 'recording') {
      this.mediaRecorder.stop();
    }
    this.stopAudioMonitor();
    if (this.silenceTimeout) {
      clearTimeout(this.silenceTimeout);
      this.silenceTimeout = null;
    }
    if (this.recordingTimeoutId) {
      clearTimeout(this.recordingTimeoutId);
      this.recordingTimeoutId = null;
    }
  }

  private async handleRecordingStop(): Promise<void> {
    const elapsed = Date.now() - this.recordingStartTime;
    const currentLoudFrames = this.loudFrameCount;

    try {
      if (this.audioChunks.length === 0) {
        this.handleDiscard("Didn't catch that");
        return;
      }

      const tooShort = elapsed < this.MIN_RECORDING_MS;
      const notEnoughLoud = currentLoudFrames < this.MIN_AUDIO_FRAMES;

      if (tooShort || notEnoughLoud) {
        this.handleDiscard("Didn't catch that");
        return;
      }

      const blob = new Blob(this.audioChunks, { type: this.currentMimeType || 'audio/webm' });
      this.audioChunks = [];

      await this.sendForTranscription(blob);

    } catch (err) {
      console.error('Recording stop error:', err);
      if (this.isRunning) {
        this.scheduleRestart('Processing error');
      }
    }
  }

  private handleDiscard(reason: string): void {
    console.log('[transcription] Discarded:', reason);
    if (this.isRunning) {
      // Restart listening after a brief pause
      this.scheduleRestart(reason);
    }
  }

  private scheduleRestart(reason: string): void {
    if (!this.isRunning) return;
    console.log('[transcription] Restarting after:', reason);
    setTimeout(() => {
      if (this.isRunning) {
        this.startRecording();
      }
    }, 500);
  }

  // ==========================================================================
  // Audio Level Monitoring (Silence Detection)
  // ==========================================================================

  private startAudioMonitor(): void {
    this.stopAudioMonitor();
    this.monitorIntervalId = window.setInterval(() => this.checkAudioLevels(), this.MONITOR_INTERVAL_MS);
  }

  private stopAudioMonitor(): void {
    if (this.monitorIntervalId !== null) {
      clearInterval(this.monitorIntervalId);
      this.monitorIntervalId = null;
    }
  }

  private checkAudioLevels(): void {
    if (!this.analyser) return;

    const dataArray = new Uint8Array(this.analyser.frequencyBinCount);
    this.analyser.getByteFrequencyData(dataArray);

    let sum = 0;
    for (let i = 0; i < dataArray.length; i++) {
      sum += dataArray[i];
    }
    const average = sum / dataArray.length / 255;

    const elapsed = Date.now() - this.recordingStartTime;
    const metMinDuration = elapsed >= this.MIN_RECORDING_MS;
    const hasLoudAudio = average >= this.MIN_AUDIO_LEVEL;

    if (hasLoudAudio) {
      this.loudFrameCount++;
      this.silenceAfterLoud = false;
      if (this.silenceTimeout) {
        clearTimeout(this.silenceTimeout);
        this.silenceTimeout = null;
      }
    } else {
      if (!this.silenceAfterLoud && this.loudFrameCount > 0) {
        this.silenceAfterLoud = true;
      }

      if (!this.silenceTimeout && metMinDuration && this.loudFrameCount >= this.MIN_AUDIO_FRAMES) {
        this.silenceTimeout = window.setTimeout(() => {
          if (this.mediaRecorder?.state === 'recording') {
            this.mediaRecorder.stop();
          }
        }, this.SILENCE_DURATION);
      }
    }
  }

  // ==========================================================================
  // Transcription
  // ==========================================================================

  private async sendForTranscription(blob: Blob): Promise<void> {
    if (!this.isRunning) return;

    try {
      const mimeExt = (this.currentMimeType || 'audio/webm').split('/')[1];
      const formData = new FormData();
      formData.append('file', blob, `audio.${mimeExt}`);

      const res = await fetch('/transcribe', {
        method: 'POST',
        body: formData,
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Transcription failed' }));
        throw new Error(err.detail || 'Transcription failed');
      }

      const data = await res.json();
      const text = (data.text || '').trim();

      if (!text) {
        this.handleDiscard("Didn't catch that");
        return;
      }

      // Check for exit commands
      if (this.isExitCommand(text)) {
        this.stop();
        return;
      }

      // Enqueue transcribed text to message queue
      enqueue({
        content: text,
        source: 'voice',
        timestamp: Date.now(),
      });

      // If not currently processing, trigger drain
      if (!isProcessing()) {
        setProcessing(true);
        // Queue drainer will pick this up
      }

    } catch (err) {
      console.error('Transcription error:', err);
      if (this.isRunning) {
        this.scheduleRestart('Transcription failed');
      }
    }
  }

  private isExitCommand(text: string): boolean {
    const EXIT_COMMANDS = ['stop listening', 'exit voice mode', 'stop voice mode', 'goodbye', 'bye'];
    const t = text.toLowerCase().trim();
    return EXIT_COMMANDS.includes(t);
  }

  // ==========================================================================
  // Cleanup
  // ==========================================================================

  private cleanup(): void {
    this.stopAudioMonitor();

    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach((t) => t.stop());
      this.mediaStream = null;
    }

    if (this.audioContext) {
      this.audioContext.close().catch(() => {});
      this.audioContext = null;
    }

    this.analyser = null;
    this.mediaRecorder = null;
    this.audioChunks = [];

    if (this.silenceTimeout) {
      clearTimeout(this.silenceTimeout);
      this.silenceTimeout = null;
    }
    if (this.recordingTimeoutId) {
      clearTimeout(this.recordingTimeoutId);
      this.recordingTimeoutId = null;
    }
  }
}

// Singleton instance for the app
let pipelineInstance: TranscriptionPipeline | null = null;

export function getTranscriptionPipeline(): TranscriptionPipeline {
  if (!pipelineInstance) {
    pipelineInstance = new TranscriptionPipeline();
  }
  return pipelineInstance;
}