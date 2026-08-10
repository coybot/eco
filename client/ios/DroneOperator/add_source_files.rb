#!/usr/bin/env ruby
# Adds one or more Swift source files to the DroneOperator app target's
# project.pbxproj, without a full `xcodegen generate` (which, as of this
# script, destructively drops the hand-added DroneOperatorUITests target and
# the shared DroneOperator.xcscheme - see add_uitest_target.rb). Mirrors that
# script's use of the `xcodeproj` gem, just for plain source files instead of
# a whole target.
#
# Idempotent: skips a path already present as a file reference anywhere in
# the project. Group nesting mirrors each file's path under DroneOperator/
# (e.g. DroneOperator/Config/Foo.swift lands in the existing "Config" group,
# creating it if it doesn't exist yet).
#
# Usage: ruby add_source_files.rb DroneOperator/Config/GCSSettings.swift [more paths...]
require 'xcodeproj'

PROJ = 'DroneOperator.xcodeproj'
abort 'usage: add_source_files.rb <path relative to project root> [...]' if ARGV.empty?

project = Xcodeproj::Project.open(PROJ)
app = project.targets.find { |t| t.name == 'DroneOperator' } or abort 'no app target'

existing_paths = project.files.map(&:real_path).map(&:to_s)

ARGV.each do |rel_path|
  abs_path = File.expand_path(rel_path)
  if existing_paths.include?(abs_path)
    warn "already present, skipping: #{rel_path}"
    next
  end
  unless File.exist?(abs_path)
    abort "file does not exist: #{abs_path}"
  end

  parts = rel_path.split('/')
  # Walk/create the group chain for everything between the source root and
  # the filename - e.g. "DroneOperator/Views/Settings/Foo.swift" resolves to
  # DroneOperator > Views > Settings, creating "Settings" if it doesn't exist
  # yet. Deliberately does NOT touch source_tree (stays the default
  # "<group>", resolved relative to the parent) and, for any NEWLY created
  # group, explicitly sets `path` to that segment's name - `find_subpath`'s
  # own group-creation default (`name` only, no `path`) resolves children
  # against the PARENT's directory instead of the new subdirectory, which
  # silently points file references at nonexistent paths (caught by
  # comparing PBXFileReference#real_path against the file actually existing
  # on disk after running this script).
  group = project.main_group
  parts[0...-1].each do |segment|
    child = group.children.find { |c| c.respond_to?(:path) && c.path == segment && c.isa == 'PBXGroup' }
    if child
      group = child
    else
      group = group.new_group(segment, segment)
      puts "created group: #{segment} (under #{rel_path})"
    end
  end
  ref = group.new_reference(parts.last)
  app.add_file_references([ref])
  puts "added: #{rel_path}"
end

project.save
