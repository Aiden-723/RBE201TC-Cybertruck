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
assertSuccess(results); % A failure or incomplete test must fail a batch run.
end
