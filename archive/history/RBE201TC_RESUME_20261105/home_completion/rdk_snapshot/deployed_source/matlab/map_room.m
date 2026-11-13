function result = map_room(csvFiles, outputDirectory)
%MAP_ROOM Map measured, stationary room runs; never create synthetic samples.
% MATLAB R2020b+. csvFiles: string array of raw CSVs or a raw-run directory.
% Output directory must not already exist. Raw inputs are never modified.
% Bench diagnostics, failures and invalid rows are saved as exclusions.
% One mean per RunID/StationID is calculated before equal-weight run averages.
% Interpolation is confined to the measured-point convex hull, NOT a room mask.

arguments
    csvFiles
    outputDirectory (1,1) string
end
if isfolder(outputDirectory) || isfile(outputDirectory)
    error('RBE:OutputExists', 'Use a new analysis output directory; no existing analysis is overwritten.');
end
files = string(csvFiles);
if isscalar(files) && isfolder(files)
    entries = dir(fullfile(files, '**', 'temperature_points_*.csv'));
    files = string(fullfile({entries.folder}, {entries.name}));
end
files = files(:);
if isempty(files)
    error('RBE:NoData', 'No raw CSVs found. No map was generated.');
end
required = ["RunID","Detection point","Trigger time","Result time", ...
    "Humidity_%RH","Temperature_°C","Status","Explanation","RunPurpose", ...
    "GroupID","RoomID","StationID","X_m","Y_m","SensorHeight_m","Delay_seconds"];
allRows = table();
for k = 1:numel(files)
    options = detectImportOptions(files(k), 'VariableNamingRule','preserve');
    if ~all(ismember(required, string(options.VariableNames)))
        error('RBE:Schema', 'Missing coordinates/purpose/schema in %s. Do not guess locations for legacy logs.', files(k));
    end
    % TextType alone does not disable datetime/numeric type inference. Preserve
    % source spellings so ISO timestamps and event identifiers can be validated.
    options = setvartype(options, cellstr(required), 'string');
    options = setvaropts(options, cellstr(required), 'WhitespaceRule','preserve');
    raw = readtable(files(k), options);
    raw = raw(:, cellstr(required));
    for field = required
        raw.(field) = string(raw.(field));
        raw.(field)(ismissing(raw.(field))) = "";
    end
    raw.SourceFile = repmat(files(k), height(raw), 1);
    allRows = [allRows; raw]; %#ok<AGROW>
end
if isempty(allRows)
    error('RBE:NoEvents', 'Input files contain no events.');
end
pointNumbers = parse_real_numbers(allRows.("Detection point"));
badIdentity = empty_or_padded(allRows.RunID) | ~isfinite(pointNumbers) | ...
    pointNumbers<1 | pointNumbers~=floor(pointNumbers) | pointNumbers>=flintmax;
% Compare numeric event identities: "1", "01" and "1.0" are the same event.
% Invalid identities are exclusions, not artificial duplicate missing keys.
keys = table(allRows.RunID(~badIdentity), pointNumbers(~badIdentity));
if height(unique(keys)) ~= height(keys)
    error('RBE:DuplicateEvent', 'Duplicate RunID/Detection point. Remove duplicate input copies; never count the same event twice.');
end
x = parse_real_numbers(allRows.X_m);
y = parse_real_numbers(allRows.Y_m);
h = parse_real_numbers(allRows.SensorHeight_m);
t = parse_real_numbers(allRows.("Temperature_°C"));
rh = parse_real_numbers(allRows.("Humidity_%RH"));
delay = parse_real_numbers(allRows.Delay_seconds);
reason = strings(height(allRows), 1);
reason(badIdentity) = "invalid_run_or_event_identifier";
known = ismember(allRows.Status, ["pending","success","sensor_failure","request_failure","timeout","cancelled"]);
reason(~known & reason=="") = "unknown_status";
reason(known & allRows.Status ~= "success" & reason=="") = "non_success_event";
reason(allRows.RunPurpose ~= "room_stationary" & reason=="") = "not_a_formal_stationary_room_run";
badLocation = empty_or_padded(allRows.StationID) | empty_or_padded(allRows.GroupID) | ...
    empty_or_padded(allRows.RoomID) | ~isfinite(x) | ~isfinite(y) | ~isfinite(h) | h<0;
reason(badLocation & reason=="") = "missing_or_invalid_measured_location";
badValue = ~isfinite(t) | t < -20 | t > 80 | ~isfinite(rh) | rh < 0 | rh > 100;
reason(badValue & reason=="") = "invalid_sensor_value";
trigger = parse_times(allRows.("Trigger time"));
finished = parse_times(allRows.("Result time"));
% Include the stated 20 ms boundary despite floating subtraction roundoff.
% The 1 ns numerical margin is not a timing accuracy/response-time claim.
badTime = isnat(trigger) | isnat(finished) | finished < trigger | ...
    ~isfinite(delay) | delay < 0 | abs(seconds(finished-trigger)-delay)>0.02+1e-9;
reason(badTime & reason=="") = "invalid_or_backwards_timestamp";
valid = reason=="";
excluded = allRows(~valid, :);
excluded.Exclusion_reason = reason(~valid);
mkdir(outputDirectory);
writetable(excluded, fullfile(outputDirectory, 'excluded_rows.csv'));
if ~any(valid)
    error('RBE:NoValidRoomData', 'No valid coordinate-linked room data. Exclusions saved; no map generated. Bench readings cannot make a room map.');
end
D = allRows(valid, :);
D.X_m = x(valid); D.Y_m = y(valid); D.SensorHeight_m = h(valid);
D.Temperature_C = t(valid); D.RH_pct = rh(valid); D.Delay_s = delay(valid);
if numel(unique(D.GroupID)) ~= 1 || numel(unique(D.RoomID)) ~= 1
    error('RBE:MixedRooms', 'Analyze one GroupID/RoomID at a time.');
end
if exceeds_location_tolerance(D.SensorHeight_m)
    error('RBE:MixedHeights', 'Sensor heights differ by more than 1 mm; analyze each measured plane separately.');
end
% Stable output order independent of CSV argument order / event row order.
stationIDs = unique(D.StationID);
for s = 1:numel(stationIDs)
    rows = D(D.StationID == stationIDs(s), :);
    if exceeds_location_tolerance(rows.X_m) || exceeds_location_tolerance(rows.Y_m)
        error('RBE:StationMoved', 'Station %s has inconsistent coordinates. Correct the association separately, never edit original observations.', stationIDs(s));
    end
end
[g, runIDs, runStations] = findgroups(D.RunID, D.StationID);
n = max(g);
rx = zeros(n,1); ry = rx; rheight = rx; rt = rx; rrh = rx; rn = rx; rd = rx;
for k = 1:n
    rows = D(g==k, :);
    rx(k) = mean(rows.X_m); ry(k) = mean(rows.Y_m);
    rheight(k) = mean(rows.SensorHeight_m);
    rt(k) = mean(rows.Temperature_C); rrh(k) = mean(rows.RH_pct);
    rn(k) = height(rows); rd(k) = mean(rows.Delay_s);
end
runMeans = table(runIDs, runStations, rx, ry, rheight, rt, rrh, rn, rd, ...
    'VariableNames', {'RunID','StationID','X_m','Y_m','SensorHeight_m', ...
    'Temperature_C','RH_pct','N_events','Mean_delay_s'});
ns = numel(stationIDs);
sx = zeros(ns,1); sy = sx; sh = sx; st = sx; srh = sx; sn = sx; se = sx; sd = sx;
ts = nan(ns,1); rhs = ts;
for k = 1:ns
    rows = runMeans(runMeans.StationID == stationIDs(k), :);
    sx(k)=mean(rows.X_m); sy(k)=mean(rows.Y_m); sh(k)=mean(rows.SensorHeight_m);
    st(k)=mean(rows.Temperature_C); srh(k)=mean(rows.RH_pct);
    sn(k)=height(rows); se(k)=sum(rows.N_events); sd(k)=mean(rows.Mean_delay_s);
    if height(rows)>=2
        ts(k)=std(rows.Temperature_C,0); rhs(k)=std(rows.RH_pct,0);
    end
end
summary = table(stationIDs,sx,sy,sh,sn,se,st,ts,srh,rhs,sd, ...
    'VariableNames', {'StationID','X_m','Y_m','SensorHeight_m','N_runs','N_events', ...
    'Temperature_mean_C','Temperature_sd_C','RH_mean_pct','RH_sd_pct','Mean_delay_s'});
if size(unique([sx sy], 'rows'),1) ~= ns
    error('RBE:DuplicateLocation', 'Different StationIDs share coordinates. Resolve measured station identities before interpolation.');
end
writetable(D, fullfile(outputDirectory, 'accepted_events.csv'));
writetable(runMeans, fullfile(outputDirectory, 'run_station_means.csv'));
writetable(summary, fullfile(outputDirectory, 'station_summary.csv'));
canInterpolate = ns>=3 && rank([sx-mean(sx), sy-mean(sy)])==2;
plot_field(summary, st, 'Temperature / deg C', 'temperature', outputDirectory, canInterpolate);
plot_field(summary, srh, 'Relative humidity / %RH', 'humidity', outputDirectory, canInterpolate);
result = struct('summary',summary,'run_station_means',runMeans, ...
    'accepted_count',height(D),'excluded_count',height(excluded), ...
    'interpolation_used',canInterpolate,'RoomID',D.RoomID(1), ...
    'GroupID',D.GroupID(1),'output_directory',outputDirectory);
save(fullfile(outputDirectory, 'analysis.mat'), 'result');
fprintf('Accepted %d events; excluded %d; %d stations. Interpolation: %d.\n', ...
    height(D), height(excluded), ns, canInterpolate);
end

function numbers = parse_real_numbers(values)
% str2double also accepts complex text; finite complex values are not physical
% coordinates, sensor readings, delays or event numbers. Exclude, do not coerce.
numbers = str2double(values);
numbers(imag(numbers)~=0) = NaN;
numbers = real(numbers);
end

function invalid = empty_or_padded(values)
% Do not silently repair identifiers or merge whitespace-only identities.
trimmed = strip(values);
invalid = strlength(trimmed)==0 | values~=trimmed;
end

function exceeded = exceeds_location_tolerance(values)
% Permit the stated 1 mm boundary despite subtraction roundoff (e.g. .501-.5).
roundoff = 8 * eps(max(1, max(abs(values))));
exceeded = max(values)-min(values) > 0.001 + roundoff;
end

function parsed = parse_times(values)
parsed = NaT(numel(values),1,'TimeZone','UTC');
% Match the logger's millisecond ISO-8601 format, with an explicit UTC/offset.
% datetime's permissive parsing must not invent a timezone for a naive value.
pattern = '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}(Z|[+-]\d{2}:\d{2})$';
for k=1:numel(values)
    if isempty(regexp(char(values(k)), pattern, 'once'))
        continue;
    end
    try
        parsed(k)=datetime(values(k),'InputFormat','yyyy-MM-dd''T''HH:mm:ss.SSSXXX','TimeZone','UTC');
    catch
        % Invalid timestamps remain NaT and are excluded, never repaired silently.
    end
end
end

function plot_field(S, values, label, stem, outputDirectory, interpolate)
f = figure('Visible','off','Color','w','Position',[100 100 1000 760]);
cleanup = onCleanup(@() close(f)); %#ok<NASGU>
ax = axes(f); hold(ax,'on');
if interpolate
    gx = linspace(min(S.X_m),max(S.X_m),150);
    gy = linspace(min(S.Y_m),max(S.Y_m),150);
    [X,Y] = meshgrid(gx,gy);
    F = scatteredInterpolant(S.X_m,S.Y_m,values,'natural','none');
    Z = F(X,Y);
    image = imagesc(ax,gx,gy,Z);
    image.AlphaData = isfinite(Z);
    ax.YDir='normal';
    hull = convhull(S.X_m,S.Y_m);
    plot(ax,S.X_m(hull),S.Y_m(hull),'k--','LineWidth',1);
    note = 'Shading = interpolation within measured-point convex hull only; walls/obstacles not modelled. Blank = no interpolation.';
else
    note = 'Measured points only: fewer than 3 non-collinear stations. No spatial interpolation.';
end
scatter(ax,S.X_m,S.Y_m,90,values,'filled','MarkerEdgeColor','k');
text(ax,S.X_m,S.Y_m,"  "+S.StationID,'Interpreter','none','VerticalAlignment','bottom');
axis(ax,'equal'); grid(ax,'on');
xlabel(ax,'X / m'); ylabel(ax,'Y / m');
title(ax,{label; 'Dots = measured station means (equal weight per run)'},'Interpreter','none');
bar = colorbar(ax); bar.Label.String=label; bar.Label.Interpreter='none';
colormap(ax,parula);
annotation(f,'textbox',[0.05 0.005 0.9 0.08],'String',note,'EdgeColor','none','Interpreter','none');
exportgraphics(f,fullfile(outputDirectory,string(stem)+".png"),'Resolution',180);
savefig(f,fullfile(outputDirectory,string(stem)+".fig"));
end
