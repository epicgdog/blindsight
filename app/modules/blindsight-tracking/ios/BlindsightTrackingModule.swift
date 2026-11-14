import ExpoModulesCore
import ARKit
import CoreMotion
import AVFoundation
import Speech
import Vision

public final class BlindsightTrackingModule: Module {
  private lazy var delegate = TrackingDelegate(owner: self)
  private let session = ARSession()
  private let pedometer = CMPedometer()
  private let voice = AVSpeechSynthesizer()
  private let context = CIContext()
  private let queue = DispatchQueue(label: "blindsight.perception", qos: .userInteractive)
  private var lastFrame = 0.0
  private var lastHazard = 0.0
  private var steps = 0
  private var sequence = 0
  private var running = false
  private var detector: VNCoreMLModel?
  private var lastDetection = 0.0
  private var watchdog: DispatchSourceTimer?
  private var recognition: SFSpeechRecognitionTask?
  private var audio: AVAudioEngine?
  private var request: SFSpeechAudioBufferRecognitionRequest?

  public func definition() -> ModuleDefinition {
    Name("BlindsightTracking")
    Events("tracking", "hazard", "question")
    AsyncFunction("start") { () -> [String: Bool] in
      guard ARWorldTrackingConfiguration.isSupported else { throw NSError(domain: "ARKit unsupported", code: 1) }
      self.sequence = 0; self.steps = 0; self.lastFrame = 0; self.running = true
      if let url = Bundle(for: TrackingDelegate.self).url(forResource: "BlindsightModels", withExtension: "bundle"),
         let bundle = Bundle(url: url),
         let modelURL = bundle.url(forResource: "ObstacleDetector", withExtension: "mlmodelc") {
        let model = try MLModel(contentsOf: modelURL)
        self.detector = try VNCoreMLModel(for: model)
      }
      self.session.delegate = self.delegate
      self.session.delegateQueue = self.queue
      let config = ARWorldTrackingConfiguration()
      let depth = ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)
      if depth { config.frameSemantics.insert(.sceneDepth) }
      self.session.run(config, options: [.resetTracking, .removeExistingAnchors])
      self.pedometer.startUpdates(from: Date()) { data, _ in
        self.queue.async { self.steps = data?.numberOfSteps.intValue ?? self.steps }
      }
      let timer = DispatchSource.makeTimerSource(queue: self.queue)
      timer.schedule(deadline: .now() + 1, repeating: .milliseconds(250))
      timer.setEventHandler { [weak self] in
        guard let self = self, self.running else { return }
        if ProcessInfo.processInfo.systemUptime - self.lastFrame > 0.5 {
          self.warn("Stop. Camera tracking is unavailable.")
        }
      }
      self.watchdog = timer; timer.resume()
      return ["worldTracking": true, "depth": depth, "objectDetection": self.detector != nil]
    }
    Function("stop") {
      self.running = false; self.session.pause(); self.pedometer.stopUpdates()
      self.watchdog?.cancel(); self.watchdog = nil
      self.stopListening()
      DispatchQueue.main.async { self.voice.stopSpeaking(at: .immediate) }
    }
    Function("listen") {
      SFSpeechRecognizer.requestAuthorization { status in
        guard status == .authorized else { self.sendEvent("question", ["error": "Speech permission denied"]); return }
        DispatchQueue.main.async { self.beginListening() }
      }
    }
    Function("stopListening") { self.stopListening() }
    OnDestroy { self.session.pause(); self.watchdog?.cancel(); self.pedometer.stopUpdates(); self.stopListening() }
  }

  private func warn(_ text: String) {
    let now = ProcessInfo.processInfo.systemUptime
    guard now - lastHazard > 2 else { return }
    lastHazard = now
    sendEvent("hazard", ["text": text, "decisionTimestampMs": now * 1000])
    // Native emergency speech remains independent of the JS bridge and network.
    DispatchQueue.main.async {
      self.voice.stopSpeaking(at: .immediate)
      let speech = AVSpeechUtterance(string: text)
      speech.rate = 0.55
      self.voice.speak(speech)
    }
  }

  public func session(_ session: ARSession, didUpdate frame: ARFrame) {
    guard running else { return }
    let now = ProcessInfo.processInfo.systemUptime
    if now - frame.timestamp > 0.2 { warn("Stop. Camera data is delayed."); return }
    let normal: Bool
    if case .normal = frame.camera.trackingState { normal = true } else { normal = false }
    var clear = false
    var hazard = false
    if let depth = frame.sceneDepth {
      let buffer = depth.depthMap
      CVPixelBufferLockBaseAddress(buffer, .readOnly)
      let w = CVPixelBufferGetWidth(buffer), h = CVPixelBufferGetHeight(buffer)
      let stride = CVPixelBufferGetBytesPerRow(buffer) / MemoryLayout<Float32>.stride
      if let base = CVPixelBufferGetBaseAddress(buffer) {
        let values = base.assumingMemoryBound(to: Float32.self)
        // Small central corridor; this is a depth stop, never proof of full body clearance.
        var valid = 0, close = 0, total = 0
        for y in strideValue(h / 4, 3 * h / 4, 3) {
          for x in strideValue(w / 3, 2 * w / 3, 3) {
            total += 1
            let d = values[y * stride + x]
            if d.isFinite && d > 0.15 { valid += 1; if d < 1.5 { close += 1 } }
          }
        }
        hazard = close > 3
        // Clearance is deliberately false until full corridor geometry is validated.
        clear = false
        if valid < total / 2 { warn("Stop. Depth coverage is insufficient.") }
      }
      CVPixelBufferUnlockBaseAddress(buffer, .readOnly)
    }
    if hazard { warn("Stop. Obstacle ahead.") }
    if !normal { warn("Stop. Tracking needs verification.") }
    // Optional bundled, NMS-enabled CoreML detector. A visual warning is not clearance.
    if let detector = detector, now - lastDetection >= 0.15 {
      lastDetection = now
      let request = VNCoreMLRequest(model: detector)
      request.imageCropAndScaleOption = .scaleFit
      let handler = VNImageRequestHandler(cvPixelBuffer: frame.capturedImage, orientation: .right)
      if (try? handler.perform([request])) != nil,
         let objects = request.results as? [VNRecognizedObjectObservation] {
        for object in objects {
          let box = object.boundingBox
          if let label = object.labels.first, label.confidence >= 0.65,
             box.midX > 0.25, box.midX < 0.75, box.height > 0.2 {
            warn("Caution. \(label.identifier) ahead.")
            break
          }
        }
      }
    }
    guard now - lastFrame >= 0.2 else { return }
    lastFrame = now; sequence += 1
    let image = CIImage(cvPixelBuffer: frame.capturedImage)
    let scale = min(1.0, 640.0 / image.extent.width)
    let scaled = image.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
    guard let jpeg = context.jpegRepresentation(of: scaled, colorSpace: CGColorSpaceCreateDeviceRGB()) else { return }
    let transform = frame.camera.transform
    let k = frame.camera.intrinsics
    var pose: [Double] = [], intrinsics: [Double] = []
    for r in 0..<4 { for c in 0..<4 { pose.append(Double(transform[c][r])) } }
    for r in 0..<3 { for c in 0..<3 { intrinsics.append(Double(k[c][r]) * (r < 2 ? scale : 1)) } }
    sendEvent("tracking", ["version": 1, "sequence": sequence, "timestamp_ms": frame.timestamp * 1000,
      "pose_age_ms": max(0, (ProcessInfo.processInfo.systemUptime - frame.timestamp) * 1000), "transform": pose,
      "tracking": normal ? "normal" : "limited", "floor": "ground", "steps": steps,
      "depth_supported": frame.sceneDepth != nil, "depth_clear": clear, "hazard": hazard,
      "jpeg_b64": jpeg.base64EncodedString(), "intrinsics": intrinsics])
  }

  public func session(_ session: ARSession, didFailWithError error: Error) { warn("Stop. Camera session failed.") }
  public func sessionWasInterrupted(_ session: ARSession) { warn("Stop. Camera interrupted.") }

  private func beginListening() {
    stopListening()
    guard let recognizer = SFSpeechRecognizer(), recognizer.isAvailable else { return }
    let engine = AVAudioEngine(), req = SFSpeechAudioBufferRecognitionRequest()
    guard recognizer.supportsOnDeviceRecognition else {
      sendEvent("question", ["error": "On-device speech recognition unavailable; use text controls."]); return
    }
    req.requiresOnDeviceRecognition = true
    do {
      let session = AVAudioSession.sharedInstance()
      try session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker, .duckOthers])
      try session.setActive(true)
      let input = engine.inputNode
      input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { buffer, _ in req.append(buffer) }
      audio = engine; request = req
      recognition = recognizer.recognitionTask(with: req) { result, error in
        if let result = result, result.isFinal { self.sendEvent("question", ["text": result.bestTranscription.formattedString]); self.stopListening() }
        if error != nil { self.stopListening() }
      }
      engine.prepare(); try engine.start()
    } catch { stopListening(); sendEvent("question", ["error": "Microphone unavailable"]) }
  }

  private func stopListening() {
    if let audio = audio { audio.stop(); audio.inputNode.removeTap(onBus: 0) }
    request?.endAudio(); recognition?.cancel()
    audio = nil; request = nil; recognition = nil
  }
}

private func strideValue(_ start: Int, _ end: Int, _ by: Int) -> StrideTo<Int> {
  return stride(from: start, to: end, by: by)
}

private final class TrackingDelegate: NSObject, ARSessionDelegate {
  weak var owner: BlindsightTrackingModule?
  init(owner: BlindsightTrackingModule) { self.owner = owner }
  func session(_ session: ARSession, didUpdate frame: ARFrame) { owner?.session(session, didUpdate: frame) }
  func session(_ session: ARSession, didFailWithError error: Error) { owner?.session(session, didFailWithError: error) }
  func sessionWasInterrupted(_ session: ARSession) { owner?.sessionWasInterrupted(session) }
}
