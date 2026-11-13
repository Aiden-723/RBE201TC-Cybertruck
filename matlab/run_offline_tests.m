function results = run_offline_tests()
%RUN_OFFLINE_TESTS Execute isolated SOFTWARE_FIXTURE_NOT_COURSE_DATA tests.
% Requires MATLAB R2020b+, matlab.unittest and working invisible-figure export.
% All fixture CSVs and map outputs belong to TemporaryFolderFixture folders.
% This function never contacts hardware and does not write to formal raw data.

sourceDirectory = fileparts(mfilename('fullpath'));
suite = matlab.unittest.TestSuite.fromFolder(fullfile(sourceDirectory, 'tests'));
assert(~isempty(suite), 'RBE:NoOfflineTests', 'No offline MATLAB tests discovered.');
runner = matlab.unittest.TestRunner.withTextOutput( ...
    'Verbosity', matlab.unittest.Verbosity.Detailed);
results = runner.run(suite);
% Emit evidence only after MATLAB has actually returned real TestResult objects.
% This marker does not create a report file or imply that fixture data are real.
summary = struct('evidence_kind','actual_matlab_unittest', ...
    'matlab_version',version,'test_count',numel(results), ...
    'passed',sum([results.Passed]),'failed',sum([results.Failed]), ...
    'incomplete',sum([results.Incomplete]),'software_fixture_only',true);
fprintf('RBE_MATLAB_TEST_SUMMARY=%s\n', jsonencode(summary));
assertSuccess(results); % A failure or incomplete test must fail a batch run.
end
