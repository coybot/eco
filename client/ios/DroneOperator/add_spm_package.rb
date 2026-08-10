#!/usr/bin/env ruby
# Adds a remote Swift Package Manager dependency to the DroneOperator app
# target, without a full `xcodegen generate` (destructive - see
# add_source_files.rb's comment). Mirrors the WebRTC/aws-sdk-ios-spm wiring
# already in project.pbxproj: a project-level XCRemoteSwiftPackageReference,
# a target-level XCSwiftPackageProductDependency, and a PBXBuildFile entry in
# the app target's Frameworks build phase.
#
# Idempotent: skips if a package reference with the same repository URL (or a
# product dependency with the same product name) already exists.
#
# Usage: ruby add_spm_package.rb <repo-url> <product-name> <min-version>
#   ruby add_spm_package.rb https://github.com/emqx/CocoaMQTT CocoaMQTT 2.1.6
require 'xcodeproj'

PROJ = 'DroneOperator.xcodeproj'
repo_url, product_name, min_version = ARGV
abort 'usage: add_spm_package.rb <repo-url> <product-name> <min-version>' unless repo_url && product_name && min_version

project = Xcodeproj::Project.open(PROJ)
app = project.targets.find { |t| t.name == 'DroneOperator' } or abort 'no app target'

existing_ref = project.root_object.package_references.find { |r| r.repositoryURL == repo_url }
if existing_ref
  warn "package reference already present: #{repo_url}"
  package_ref = existing_ref
else
  package_ref = project.new(Xcodeproj::Project::Object::XCRemoteSwiftPackageReference)
  package_ref.repositoryURL = repo_url
  package_ref.requirement = { 'kind' => 'upToNextMajorVersion', 'minimumVersion' => min_version }
  project.root_object.package_references << package_ref
  puts "added package reference: #{repo_url}"
end

existing_dep = app.package_product_dependencies.find { |d| d.product_name == product_name }
if existing_dep
  warn "product dependency already present: #{product_name}"
else
  dep = project.new(Xcodeproj::Project::Object::XCSwiftPackageProductDependency)
  dep.package = package_ref
  dep.product_name = product_name
  app.package_product_dependencies << dep

  frameworks_phase = app.frameworks_build_phase
  build_file = project.new(Xcodeproj::Project::Object::PBXBuildFile)
  build_file.product_ref = dep
  frameworks_phase.files << build_file
  puts "added product dependency + framework build file: #{product_name}"
end

project.save
