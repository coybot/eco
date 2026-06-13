#!/usr/bin/env ruby
# Adds the missing DroneOperatorUITests UI-testing target to the project and
# wires the existing shared scheme's testable to it. Idempotent-ish: skips if a
# target with the name already exists.
require 'xcodeproj'

PROJ = 'DroneOperator.xcodeproj'
NAME = 'DroneOperatorUITests'
project = Xcodeproj::Project.open(PROJ)
app = project.targets.find { |t| t.name == 'DroneOperator' } or abort 'no app target'

existing = project.targets.find { |t| t.name == NAME }
if existing
  warn "#{NAME} already exists (#{existing.uuid})"
  test = existing
else
  test = project.new_target(:ui_test_bundle, NAME, :ios, '17.0')
  group = project.main_group.find_subpath(NAME, true)
  group.set_source_tree('SOURCE_ROOT')
  ref = group.new_reference("#{NAME}/#{NAME}.swift")
  test.add_file_references([ref])
  test.add_dependency(app)
end

test.build_configurations.each do |c|
  c.build_settings['PRODUCT_BUNDLE_IDENTIFIER'] = 'us.astral.drone.uitests'
  c.build_settings['TEST_TARGET_NAME'] = 'DroneOperator'
  c.build_settings['IPHONEOS_DEPLOYMENT_TARGET'] = '17.0'
  c.build_settings['SWIFT_VERSION'] = '5.9'
  c.build_settings['GENERATE_INFOPLIST_FILE'] = 'YES'
  c.build_settings['CODE_SIGNING_ALLOWED'] = 'NO'
  c.build_settings['TARGETED_DEVICE_FAMILY'] = '1,2'
  c.build_settings['SWIFT_EMIT_LOC_STRINGS'] = 'NO'
end

project.save
puts "TEST_TARGET_UUID=#{test.uuid}"
