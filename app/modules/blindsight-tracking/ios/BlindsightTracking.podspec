Pod::Spec.new do |s|
  s.name = 'BlindsightTracking'
  s.version = '1.0.0'
  s.summary = 'ARKit tracking and local depth stops for Blindsight'
  s.description = s.summary
  s.license = { :type => 'MIT' }
  s.author = 'Blindsight'
  s.homepage = 'https://github.com/epicgdog/insight'
  s.platforms = { :ios => '16.0' }
  s.source = { :git => 'https://github.com/epicgdog/insight.git' }
  s.static_framework = true
  s.dependency 'ExpoModulesCore'
  s.frameworks = 'ARKit', 'CoreMotion', 'AVFoundation', 'Speech', 'Vision'
  s.source_files = '**/*.swift'
  s.resource_bundles = { 'BlindsightModels' => ['Models/*.mlmodelc'] }
  s.swift_version = '5.9'
end
