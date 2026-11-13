classdef TestMapRoom < matlab.unittest.TestCase
    %TESTMAPROOM Offline software tests, not room measurements or course maps.
    % Every CSV and generated analysis is SOFTWARE_FIXTURE_NOT_COURSE_DATA.
    % TemporaryFolderFixture owns cleanup; no formal raw directory is used.

    properties (Access = private)
        FixtureRoot
        InputSequence = 0
        OutputSequence = 0
    end

    methods (TestMethodSetup)
        function isolateSoftwareFixtures(testCase)
            sourceDirectory = fileparts(fileparts(mfilename('fullpath')));
            testCase.applyFixture(matlab.unittest.fixtures.PathFixture(sourceDirectory));
            temporary = testCase.applyFixture(matlab.unittest.fixtures.TemporaryFolderFixture);
            testCase.FixtureRoot = fullfile(string(temporary.Folder), ...
                "SOFTWARE_FIXTURE_NOT_COURSE_DATA");
            mkdir(testCase.FixtureRoot);
        end
    end

    methods (Test)
        function rejectsFractionsRoundedToIntegersAndExtremeExponents(testCase)
            rows = fixture_events(6);
            rows.("Detection point") = ["1.0000000000000001"; ...
                "9007199254740990.5"; "9.0071992547409905e15"; ...
                "1e1000000000"; "1e-1000000000"; "1e" + string(repmat('9', 1, 300))];
            testCase.verifyAllExcluded(rows, "invalid_run_or_event_identifier");
        end

        function acceptsExactIntegerTextBeforeBinary64Conversion(testCase)
            rows = fixture_events(7);
            rows.RunID = rows.RunID + "_" + string((1:height(rows))');
            rows.("Detection point") = ["0001"; "+1.0"; "100e-2"; ".01e2"; ...
                "1e+0000000000000000000000000"; "9007199254740991"; ...
                "9.007199254740991e15"];
            result = testCase.analyze(rows);
            testCase.verifyEqual(result.accepted_count, 7);
            testCase.verifyEqual(result.excluded_count, 0);
            testCase.verifyEqual(result.summary.N_runs, 7);
        end

        function rejectsEquivalentScientificEventAliases(testCase)
            rows = fixture_events(3);
            rows.("Detection point") = ["1"; "100e-2"; ".01e2"];
            source = testCase.writeFixture(rows);
            destination = testCase.nextOutput();
            testCase.verifyError(@() map_room(source, destination), 'RBE:DuplicateEvent');
            testCase.verifyFalse(isfolder(destination));
        end

        function rejectsEmptyMissingAndBlankOutputBeforeReadingInput(testCase)
            paths = [""; "   "; string(missing)];
            missingInput = fullfile(testCase.FixtureRoot, "SOFTWARE_FIXTURE_NO_SUCH_INPUT.csv");
            for k = 1:numel(paths)
                testCase.verifyError(@() map_room(missingInput, paths(k)), 'RBE:OutputPath');
            end
            testCase.verifyFalse(isfile(missingInput));
        end

        function failedDirectoryCreationCannotModifyRawInput(testCase)
            source = testCase.writeFixture(fixture_events(1));
            original = fileread(source);
            destination = fullfile(source, "SOFTWARE_FIXTURE_CANNOT_CREATE_CHILD");
            testCase.verifyError(@() map_room(source, destination), 'RBE:OutputCreate');
            testCase.verifyEqual(fileread(source), original);
            testCase.verifyFalse(isfolder(destination));
        end

        function rejectsDuplicateInputCopies(testCase)
            rows = fixture_events(1);
            first = testCase.writeFixture(rows);
            second = testCase.writeFixture(rows);
            destination = testCase.nextOutput();
            testCase.verifyError(@() map_room([first; second], destination), 'RBE:DuplicateEvent');
            testCase.verifyFalse(isfolder(destination));
        end

        function rejectsNumericAliasesOfSameEvent(testCase)
            rows = fixture_events(3);
            rows.("Detection point") = ["1"; "01"; "1.0"];
            source = testCase.writeFixture(rows);
            destination = testCase.nextOutput();
            testCase.verifyError(@() map_room(source, destination), 'RBE:DuplicateEvent');
            testCase.verifyFalse(isfolder(destination));
        end

        function excludesBenchAndAllNonSuccessStatuses(testCase)
            rows = fixture_events(8);
            rows.RunPurpose(2) = "bench_diagnostic";
            rows.X_m(2) = "";
            rows.Status(3:8) = ["pending"; "sensor_failure"; "request_failure"; ...
                "timeout"; "cancelled"; "unexpected_status"];
            rows.("Temperature_°C")(3:8) = "";
            rows.("Humidity_%RH")(3:8) = "";
            result = testCase.analyze(rows);
            testCase.verifyEqual(result.accepted_count, 1);
            testCase.verifyEqual(result.excluded_count, 7);
            excluded = read_output(result, 'excluded_rows.csv');
            testCase.verifyEqual(excluded.Exclusion_reason(1), "not_a_formal_stationary_room_run");
            testCase.verifyEqual(excluded.Exclusion_reason(2:6), repmat("non_success_event", 5, 1));
            testCase.verifyEqual(excluded.Exclusion_reason(7), "unknown_status");
            testCase.verifyEqual(result.summary.Temperature_mean_C, 20);
        end

        function benchOnlyCannotGenerateMap(testCase)
            rows = fixture_events(1);
            rows.RunPurpose(:) = "bench_diagnostic";
            rows.X_m(:) = "";
            rows.Y_m(:) = "";
            testCase.verifyAllExcluded(rows, "not_a_formal_stationary_room_run");
        end

        function refusesMissingAndNonfiniteLocations(testCase)
            rows = fixture_events(10);
            rows.X_m(1) = "";
            rows.X_m(2) = "NaN";
            rows.Y_m(3) = "Inf";
            rows.SensorHeight_m(4) = "-0.01";
            rows.SensorHeight_m(5) = "";
            rows.StationID(6) = "";
            rows.GroupID(7) = "   ";
            rows.RoomID(8) = "";
            rows.StationID(9) = " " + rows.StationID(9);
            rows.GroupID(10) = rows.GroupID(10) + " ";
            testCase.verifyAllExcluded(rows, "missing_or_invalid_measured_location");
        end

        function invalidEventIdentitiesAreExcludedNotMerged(testCase)
            rows = fixture_events(8);
            rows.RunID(1:2) = "";
            rows.("Detection point")(1:2) = "1";
            rows.RunID(3) = "   ";
            rows.("Detection point")(4:8) = ["0"; "1.5"; "NaN"; "Inf"; "9007199254740992"];
            testCase.verifyAllExcluded(rows, "invalid_run_or_event_identifier");
        end

        function rejectsMixedGroups(testCase)
            rows = fixture_events(2);
            rows.GroupID(2) = rows.GroupID(2) + "_OTHER";
            testCase.verifyRejectedAnalysis(rows, 'RBE:MixedRooms');
        end

        function rejectsMixedRooms(testCase)
            rows = fixture_events(2);
            rows.RoomID(2) = rows.RoomID(2) + "_OTHER";
            testCase.verifyRejectedAnalysis(rows, 'RBE:MixedRooms');
        end

        function rejectsMixedMeasurementHeights(testCase)
            rows = fixture_events(2);
            rows.SensorHeight_m = ["0.5"; "0.502"];
            testCase.verifyRejectedAnalysis(rows, 'RBE:MixedHeights');
        end

        function rejectsMovedStationInX(testCase)
            rows = fixture_events(2);
            rows.RunID(2) = rows.RunID(2) + "_SECOND_RUN";
            rows.X_m(2) = "0.002";
            testCase.verifyRejectedAnalysis(rows, 'RBE:StationMoved');
        end

        function rejectsMovedStationInY(testCase)
            rows = fixture_events(2);
            rows.Y_m(2) = "0.002";
            testCase.verifyRejectedAnalysis(rows, 'RBE:StationMoved');
        end

        function acceptsOneMillimetreBoundaryDespiteRoundoff(testCase)
            rows = fixture_events(2);
            rows.X_m = ["0.5"; "0.501"];
            rows.Y_m = ["0.5"; "0.501"];
            rows.SensorHeight_m = ["0.5"; "0.501"];
            result = testCase.analyze(rows);
            testCase.verifyEqual(result.accepted_count, 2);
            testCase.verifyEqual(result.summary.X_m, 0.5005, 'AbsTol', 1e-12);
            testCase.verifyEqual(result.summary.SensorHeight_m, 0.5005, 'AbsTol', 1e-12);
        end

        function rejectsDifferentStationIDsAtSameLocation(testCase)
            rows = fixture_events(2);
            rows.StationID(2) = rows.StationID(2) + "_OTHER";
            testCase.verifyRejectedAnalysis(rows, 'RBE:DuplicateLocation');
        end

        function computesEqualRunWeightsAndBetweenRunSampleSD(testCase)
            rows = fixture_events(4);
            rows.RunID(4) = rows.RunID(4) + "_SECOND_RUN";
            rows.("Detection point")(4) = "1"; % Event numbering restarts per RunID.
            rows.("Temperature_°C") = ["10"; "20"; "30"; "50"];
            rows.("Humidity_%RH") = ["10"; "20"; "30"; "70"];
            rows.("Result time")(4) = "2026-11-04T00:00:03.000+00:00";
            rows.Delay_seconds(4) = "3";
            result = testCase.analyze(rows);
            summary = result.summary;
            testCase.verifyEqual(summary.N_runs, 2);
            testCase.verifyEqual(summary.N_events, 4);
            testCase.verifyEqual(summary.Temperature_mean_C, 35, 'AbsTol', 1e-12);
            testCase.verifyEqual(summary.RH_mean_pct, 45, 'AbsTol', 1e-12);
            testCase.verifyEqual(summary.Mean_delay_s, 2, 'AbsTol', 1e-12);
            testCase.verifyEqual(summary.Temperature_sd_C, std([20; 50], 0), 'AbsTol', 1e-12);
            testCase.verifyEqual(summary.RH_sd_pct, std([20; 70], 0), 'AbsTol', 1e-12);
            testCase.verifyEqual(sort(result.run_station_means.N_events), [1; 3]);
        end

        function absentRunStationIsNotImputedAsZero(testCase)
            rows = fixture_events(3);
            rows.RunID(2) = rows.RunID(2) + "_SECOND_RUN";
            rows.("Detection point")(2) = "1";
            rows.StationID(3) = rows.StationID(3) + "_B";
            rows.X_m(3) = "2";
            rows.("Temperature_°C") = ["20"; "40"; "50"];
            result = testCase.analyze(rows);
            testCase.verifyEqual(result.summary.Temperature_mean_C, [30; 50], 'AbsTol', 1e-12);
            testCase.verifyEqual(result.summary.N_runs, [2; 1]);
            testCase.verifyEqual(result.summary.N_events, [2; 1]);
            testCase.verifyTrue(isnan(result.summary.Temperature_sd_C(2)));
        end

        function failuresOnlyCannotGenerateMap(testCase)
            rows = fixture_events(2);
            rows.Status = ["sensor_failure"; "timeout"];
            rows.("Temperature_°C")(:) = "";
            rows.("Humidity_%RH")(:) = "";
            testCase.verifyAllExcluded(rows, "non_success_event");
        end

        function repeatedEventsInOneRunDoNotProveRepeatability(testCase)
            result = testCase.analyze(fixture_events(3));
            testCase.verifyEqual(result.summary.N_runs, 1);
            testCase.verifyEqual(result.summary.N_events, 3);
            testCase.verifyTrue(isnan(result.summary.Temperature_sd_C));
            testCase.verifyTrue(isnan(result.summary.RH_sd_pct));
        end

        function excludesComplexNumbersInEveryNumericField(testCase)
            fields = ["Detection point", "X_m", "Y_m", "SensorHeight_m", ...
                "Temperature_°C", "Humidity_%RH", "Delay_seconds"];
            reasons = ["invalid_run_or_event_identifier", ...
                repmat("missing_or_invalid_measured_location", 1, 3), ...
                repmat("invalid_sensor_value", 1, 2), "invalid_or_backwards_timestamp"];
            for k = 1:numel(fields)
                rows = fixture_events(1);
                rows.(fields(k))(:) = "1+2i";
                testCase.verifyAllExcluded(rows, reasons(k));
            end
        end

        function runWeightsAreLocalToEachStationAndInputOrderIndependent(testCase)
            rows = fixture_events(6);
            rows.StationID([2 4 6]) = rows.StationID([2 4 6]) + "_B";
            rows.X_m([2 4 6]) = "2";
            rows.RunID(5:6) = rows.RunID(5:6) + "_SECOND_RUN";
            rows.("Detection point")(5:6) = ["1"; "2"];
            rows.("Temperature_°C") = ["10"; "20"; "30"; "40"; "50"; "60"];
            first = testCase.writeFixture(rows(1:4, :));
            second = testCase.writeFixture(rows(5:6, :));
            result = map_room([first; second], testCase.nextOutput());
            reversed = map_room([second; first], testCase.nextOutput());
            reordered = testCase.analyze(rows(end:-1:1, :));
            testCase.verifyEqual(result.summary, reversed.summary);
            testCase.verifyEqual(result.summary, reordered.summary);
            testCase.verifyEqual(result.run_station_means, reordered.run_station_means);
            testCase.verifyEqual(result.summary.Temperature_mean_C, [35; 45], 'AbsTol', 1e-12);
            testCase.verifyEqual(result.summary.N_runs, [2; 2]);
            testCase.verifyEqual(result.summary.N_events, [3; 3]);
        end

        function excludesMalformedNaiveBackwardsAndMismatchedTimes(testCase)
            rows = fixture_events(12);
            rows.("Trigger time")(2) = "2026-11-04T00:00:00.000";
            rows.("Result time")(3) = "not-a-time";
            rows.("Result time")(4) = "2026-11-03T23:59:59.000+00:00";
            rows.Delay_seconds(5) = "-1";
            rows.Delay_seconds(6) = "2";
            rows.Delay_seconds(7) = "NaN";
            rows.("Trigger time")(8) = "";
            rows.("Result time")(9) = "2026-11-04T00:00:01+00:00";
            rows.("Result time")(10) = "2026-13-04T00:00:01.000+00:00";
            rows.("Trigger time")(11) = "2026-11-04T08:00:00.000+08:00";
            rows.("Result time")(11) = "2026-11-04T00:00:01.000Z";
            rows.("Result time")(12) = "2026-11-04T00:00:01.021+00:00";
            result = testCase.analyze(rows);
            testCase.verifyEqual(result.accepted_count, 2);
            testCase.verifyEqual(result.excluded_count, 10);
            excluded = read_output(result, 'excluded_rows.csv');
            testCase.verifyEqual(excluded.Exclusion_reason, ...
                repmat("invalid_or_backwards_timestamp", 10, 1));
        end

        function allowsTimestampRoundingAtTwentyMillisecondBoundary(testCase)
            rows = fixture_events(2);
            rows.("Result time") = ["2026-11-04T00:00:01.019+00:00"; ...
                "2026-11-04T00:00:01.020+00:00"];
            result = testCase.analyze(rows);
            testCase.verifyEqual(result.accepted_count, 2);
        end

        function excludesInvalidSensorValuesButAcceptsRealZero(testCase)
            rows = fixture_events(7);
            rows.("Temperature_°C") = ["0"; "NaN"; "-21"; "81"; "20"; "20"; "20"];
            rows.("Humidity_%RH") = ["0"; "50"; "50"; "50"; "-1"; "101"; "Inf"];
            result = testCase.analyze(rows);
            testCase.verifyEqual(result.accepted_count, 1);
            testCase.verifyEqual(result.excluded_count, 6);
            testCase.verifyEqual(result.summary.Temperature_mean_C, 0);
            testCase.verifyEqual(result.summary.RH_mean_pct, 0);
            excluded = read_output(result, 'excluded_rows.csv');
            testCase.verifyEqual(excluded.Exclusion_reason, repmat("invalid_sensor_value", 6, 1));
        end

        function oneStationProducesMeasuredPointsOnly(testCase)
            result = testCase.analyze(fixture_events(1));
            testCase.verifyFalse(result.interpolation_used);
            testCase.verifyNoRaster(result);
        end

        function twoStationsProduceMeasuredPointsOnly(testCase)
            rows = station_fixture([0; 2], [0; 1]);
            result = testCase.analyze(rows);
            testCase.verifyFalse(result.interpolation_used);
            testCase.verifyNoRaster(result);
        end

        function threeCollinearStationsDoNotInterpolate(testCase)
            rows = station_fixture([0; 1; 2], [0; 1; 2]);
            result = testCase.analyze(rows);
            testCase.verifyFalse(result.interpolation_used);
            testCase.verifyNoRaster(result);
        end

        function triangularHullLeavesExteriorNaNAndTransparentInBothFields(testCase)
            rows = station_fixture([0; 2; 0], [0; 0; 2]);
            rows.("Temperature_°C") = ["20"; "25"; "30"];
            rows.("Humidity_%RH") = ["40"; "50"; "60"];
            result = testCase.analyze(rows);
            testCase.verifyTrue(result.interpolation_used);
            for stem = ["temperature", "humidity"]
                figureHandle = openfig(fullfile(result.output_directory, stem + ".fig"), 'invisible');
                cleanup = onCleanup(@() close(figureHandle)); %#ok<NASGU>
                raster = findobj(figureHandle, 'Type', 'image');
                testCase.assertEqual(numel(raster), 1);
                Z = raster.CData;
                alpha = raster.AlphaData;
                [X, Y] = meshgrid(linspace(0, 2, size(Z, 2)), linspace(0, 2, size(Z, 1)));
                outside = X + Y > 2 + 1e-12;
                inside = X > 0.1 & Y > 0.1 & X + Y < 1.9;
                testCase.assertTrue(any(outside(:)) && any(inside(:)));
                testCase.verifyTrue(all(isnan(Z(outside))));
                testCase.verifyTrue(all(alpha(outside) == 0));
                testCase.verifyTrue(all(isfinite(Z(inside))));
                testCase.verifyTrue(all(alpha(inside) == 1));
                clear cleanup;
            end
        end

        function preservesRawCSVAndWritesAuditableOutputs(testCase)
            source = testCase.writeFixture(fixture_events(1));
            original = fileread(source);
            result = map_room(source, testCase.nextOutput());
            testCase.verifyEqual(fileread(source), original);
            expected = ["excluded_rows.csv", "accepted_events.csv", ...
                "run_station_means.csv", "station_summary.csv", "analysis.mat", ...
                "temperature.png", "temperature.fig", "humidity.png", "humidity.fig"];
            testCase.verifyTrue(all(isfile(fullfile(result.output_directory, expected))));
            saved = load(fullfile(result.output_directory, 'analysis.mat'), 'result');
            testCase.verifyEqual(saved.result.summary, result.summary);
            accepted = read_output(result, 'accepted_events.csv');
            testCase.verifyEqual(accepted.Explanation, "SOFTWARE_FIXTURE_NOT_COURSE_DATA");
            testCase.verifyEqual(accepted.SourceFile, source);
        end

        function refusesExistingDirectoryAndExistingFileWithoutMutation(testCase)
            source = testCase.writeFixture(fixture_events(1));
            original = fileread(source);
            testCase.verifyError(@() map_room(source, testCase.FixtureRoot), 'RBE:OutputExists');
            testCase.verifyError(@() map_room(source, source), 'RBE:OutputExists');
            testCase.verifyEqual(fileread(source), original);
        end

        function rejectsLegacySchemaWithoutGuessingCoordinates(testCase)
            rows = fixture_events(1);
            rows.X_m = [];
            source = testCase.writeFixture(rows);
            destination = testCase.nextOutput();
            testCase.verifyError(@() map_room(source, destination), 'RBE:Schema');
            testCase.verifyFalse(isfolder(destination));
        end

        function rejectsHeaderOnlyCSV(testCase)
            rows = fixture_events(1);
            source = testCase.writeFixture(rows([], :));
            destination = testCase.nextOutput();
            testCase.verifyError(@() map_room(source, destination), 'RBE:NoEvents');
            testCase.verifyFalse(isfolder(destination));
        end

        function rejectsDirectoryWithNoCSV(testCase)
            destination = testCase.nextOutput();
            testCase.verifyError(@() map_room(testCase.FixtureRoot, destination), 'RBE:NoData');
            testCase.verifyFalse(isfolder(destination));
        end

        function discoversCSVUnderRunSubdirectories(testCase)
            rows = fixture_events(1);
            runDirectory = fullfile(testCase.FixtureRoot, "SOFTWARE_FIXTURE_NOT_COURSE_DATA_RUN");
            mkdir(runDirectory);
            source = fullfile(runDirectory, "temperature_points_SOFTWARE_FIXTURE_NOT_COURSE_DATA.csv");
            writetable(rows, source, 'Encoding', 'UTF-8');
            result = map_room(testCase.FixtureRoot, testCase.nextOutput());
            testCase.verifyEqual(result.accepted_count, 1);
        end
    end

    methods (Access = private)
        function source = writeFixture(testCase, rows)
            testCase.InputSequence = testCase.InputSequence + 1;
            source = fullfile(testCase.FixtureRoot, sprintf( ...
                'temperature_points_SOFTWARE_FIXTURE_NOT_COURSE_DATA_%03d.csv', ...
                testCase.InputSequence));
            writetable(rows, source, 'Encoding', 'UTF-8');
        end

        function destination = nextOutput(testCase)
            testCase.OutputSequence = testCase.OutputSequence + 1;
            destination = fullfile(testCase.FixtureRoot, sprintf( ...
                'SOFTWARE_FIXTURE_NOT_COURSE_DATA_analysis_%03d', testCase.OutputSequence));
        end

        function result = analyze(testCase, rows)
            result = map_room(testCase.writeFixture(rows), testCase.nextOutput());
        end

        function verifyAllExcluded(testCase, rows, expectedReason)
            source = testCase.writeFixture(rows);
            destination = testCase.nextOutput();
            testCase.verifyError(@() map_room(source, destination), 'RBE:NoValidRoomData');
            excluded = read_output(struct('output_directory', destination), 'excluded_rows.csv');
            testCase.verifyEqual(height(excluded), height(rows));
            testCase.verifyEqual(excluded.Exclusion_reason, repmat(expectedReason, height(rows), 1));
            testCase.verifyNoAnalysis(destination);
        end

        function verifyRejectedAnalysis(testCase, rows, errorID)
            source = testCase.writeFixture(rows);
            destination = testCase.nextOutput();
            testCase.verifyError(@() map_room(source, destination), errorID);
            testCase.verifyTrue(isfile(fullfile(destination, 'excluded_rows.csv')));
            testCase.verifyNoAnalysis(destination);
        end

        function verifyNoAnalysis(testCase, destination)
            forbidden = ["accepted_events.csv", "run_station_means.csv", "station_summary.csv", ...
                "temperature.png", "temperature.fig", "humidity.png", "humidity.fig", "analysis.mat"];
            testCase.verifyFalse(any(isfile(fullfile(destination, forbidden))));
        end

        function verifyNoRaster(testCase, result)
            for stem = ["temperature", "humidity"]
                figureHandle = openfig(fullfile(result.output_directory, stem + ".fig"), 'invisible');
                cleanup = onCleanup(@() close(figureHandle)); %#ok<NASGU>
                testCase.verifyEmpty(findobj(figureHandle, 'Type', 'image'));
                measured = findobj(figureHandle, 'Type', 'scatter');
                testCase.assertEqual(numel(measured), 1);
                testCase.verifyEqual(numel(measured.XData), height(result.summary));
                clear cleanup;
            end
        end
    end
end

function rows = fixture_events(count)
% All data below are deliberately synthetic software fixtures, never observations.
marker = "SOFTWARE_FIXTURE_NOT_COURSE_DATA";
repeat = @(value) repmat(string(value), count, 1);
rows = table(repeat(marker + "_RUN_A"), string((1:count)'), ...
    repeat("2026-11-04T00:00:00.000+00:00"), repeat("2026-11-04T00:00:01.000+00:00"), ...
    repeat("50"), repeat("20"), repeat("success"), repeat(marker), ...
    repeat("room_stationary"), repeat(marker + "_GROUP"), repeat(marker + "_ROOM"), ...
    repeat(marker + "_STATION_A"), repeat("0"), repeat("0"), repeat("0.5"), repeat("1"), ...
    'VariableNames', {'RunID', 'Detection point', 'Trigger time', 'Result time', ...
    'Humidity_%RH', 'Temperature_°C', 'Status', 'Explanation', 'RunPurpose', ...
    'GroupID', 'RoomID', 'StationID', 'X_m', 'Y_m', 'SensorHeight_m', 'Delay_seconds'});
end

function rows = station_fixture(x, y)
rows = fixture_events(numel(x));
rows.StationID = "SOFTWARE_FIXTURE_NOT_COURSE_DATA_STATION_" + string((1:numel(x))');
rows.X_m = string(x);
rows.Y_m = string(y);
end

function rows = read_output(result, filename)
% Import audit CSV text explicitly, just as the production importer does.
path = fullfile(result.output_directory, filename);
options = detectImportOptions(path, 'VariableNamingRule', 'preserve');
options = setvartype(options, options.VariableNames, 'string');
rows = readtable(path, options);
end
