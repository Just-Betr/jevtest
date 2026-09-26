#!/usr/bin/env ruby
# Regenerates jevtest/ios_agent/JevAgent.xcodeproj (checked in, so users don't need Ruby).
# Usage: ruby scripts/generate_ios_agent_project.rb   (needs `gem install xcodeproj`)

require 'fileutils'
require 'xcodeproj'

root = File.expand_path('../jevtest/ios_agent', __dir__)
path = File.join(root, 'JevAgent.xcodeproj')
FileUtils.rm_rf(path)

project = Xcodeproj::Project.new(path)
host = project.new_target(:application, 'JevAgentHost', :ios, '17.0', nil, :swift)
tests = project.new_target(:ui_test_bundle, 'JevAgentUITests', :ios, '17.0', nil, :swift)
tests.add_dependency(host)

host.add_file_references([project.main_group.new_group('AgentHost', 'AgentHost').new_file('AppDelegate.swift')])
ui_tests = project.main_group.new_group('AgentUITests', 'AgentUITests')
tests.add_file_references(Dir.children(File.join(root, 'AgentUITests')).grep(/\.swift\z/).sort.map { |f| ui_tests.new_file(f) })

project.targets.each do |t|
  t.build_configurations.each do |c|
    s = c.build_settings
    s['SWIFT_VERSION'] = '5.0'
    s['GENERATE_INFOPLIST_FILE'] = 'YES'
    s['CODE_SIGNING_ALLOWED'] = 'NO'
    s['CODE_SIGN_IDENTITY'] = ''
    s['IPHONEOS_DEPLOYMENT_TARGET'] = '17.0'
    s['PRODUCT_NAME'] = '$(TARGET_NAME)'
    # Bundle ids must be unique across all Apple developers, so a device build passes its team:
    # JEVTEST_TEAM_SUFFIX=.TEAMID (empty for the simulator, which needs no signing).
    s['PRODUCT_BUNDLE_IDENTIFIER'] = "dev.jevtest.agent$(JEVTEST_TEAM_SUFFIX).#{t.name}"
  end
end
host.build_configurations.each { |c| c.build_settings['INFOPLIST_KEY_UILaunchScreen_Generation'] = 'YES' }
tests.build_configurations.each { |c| c.build_settings['TEST_TARGET_NAME'] = 'JevAgentHost' }

scheme = Xcodeproj::XCScheme.new
scheme.configure_with_targets(host, tests, launch_target: true)
scheme.save_as(path, 'JevAgent', true)
project.save
puts "wrote #{path}"
