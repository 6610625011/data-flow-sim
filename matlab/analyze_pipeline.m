function R = analyze_pipeline(source, start_local, stop_local)
%ANALYZE_PIPELINE  Pull the data from the IoT system and run the Chapter-3 analysis + ML.
%
%   Plain MATLAB (no toolbox; also runs in GNU Octave).  Examples:
%     R = analyze_pipeline                                  % API of this computer, last 400 days
%     R = analyze_pipeline('http://localhost:1880', '2026-01-01T00:00', '2026-12-31T23:59')
%     R = analyze_pipeline('https://<pc>.<tailnet>.ts.net', ...)   % the dashboard via Tailscale
%     R = analyze_pipeline('../datasets/batch_30d')         % a pvsim batch folder, no server
%
%   source = URL  -> GET <url>/api/export.csv?...&m=all&every=1m (Node-RED -> InfluxDB),
%                    i.e. exactly the data the boards (or the dataset replay) sent through
%                    MQTT, checked by Node-RED and stored in InfluxDB, resampled to 1 min.
%          = folder of pvsim `batch` CSVs -> read directly (same columns are derived).
%
%   Analysis (proposal scope 9-11):
%     1. energy per day and channel, energy gain of the tracker, data completeness
%     2. 15-minute-ahead power forecast per channel: persistence and linear regression
%        (baselines) vs Random Forest regression (rf_train.m); MAE, RMSE, R^2, skill
%     3. condition classification (normal / shading levels / other faults) with Random
%        Forest classification; precision, recall, F1, confusion matrix
%   Train/test split by whole days (last 25 % of the days are the test set), so no
%   minute of a test day is ever seen in training.
%
%   Outputs: printed tables, PNG figures and CSV tables in ./out, and the struct R.

here = fileparts(mfilename('fullpath'));
outdir = fullfile(here, 'out');
if ~exist(outdir, 'dir'), mkdir(outdir); end
if nargin < 1 || isempty(source), source = 'http://localhost:1880'; end
if nargin < 2 || isempty(start_local), start_local = '2026-01-01T00:00'; end
if nargin < 3 || isempty(stop_local), stop_local = '2027-01-31T00:00'; end

% ------------------------------------------------------------------ load
if is_url(source)
    D = load_api(source, start_local, stop_local, outdir);
else
    D = load_batch_folder(source);
end
fprintf('\nloaded %d rows (%d fixed, %d track), %d days, labels: %s\n', numel(D.t), ...
        sum(D.ch == 1), sum(D.ch == 2), numel(unique(D.day)), strjoin(unique(D.label)', ', '));
R.data = D;

% ------------------------------------------------------------------ 1. energy
days = unique(D.day);
E = zeros(numel(days), 4);   % fixed out, track out, track motor, completeness [%]
for k = 1:numel(days)
    for c = 1:2
        m = D.day == days(k) & D.ch == c;
        [ts, o] = sort(D.t(m)); p = D.p_out(m); p = p(o);
        dtv = diff(ts);
        ok = dtv <= 3 * 60;                          % 1-min data: integrate gaps up to 3 min
        pa = p(1:end - 1); pb = p(2:end);
        E(k, c) = sum((pa(ok) + pb(ok)) / 2 .* dtv(ok)) / 3600;
        if c == 2 && any(~isnan(D.p_mot(m)))
            pm = D.p_mot(m); pm = pm(o); pm(isnan(pm)) = 0;
            pa = pm(1:end - 1); pb = pm(2:end);
            E(k, 3) = sum((pa(ok) + pb(ok)) / 2 .* dtv(ok)) / 3600;
        end
    end
    mf = D.day == days(k) & D.ch == 1;
    span = (max(D.t(mf)) - min(D.t(mf))) / 60 + 1;
    E(k, 4) = 100 * sum(mf) / max(span, 1);
end
gain = 100 * (E(:, 2) ./ max(E(:, 1), 1e-9) - 1);
gain_net = 100 * ((E(:, 2) - E(:, 3)) ./ max(E(:, 1), 1e-9) - 1);
gain(E(:, 1) < 1) = NaN; gain_net(E(:, 1) < 1) = NaN;     % night-only / empty days have no gain
fprintf('\n%-12s %10s %10s %9s %9s %9s %8s\n', 'day', 'E_fix[Wh]', 'E_trk[Wh]', 'E_mot', 'gain%', 'net%', 'rows%');
fid = fopen(fullfile(outdir, 'energy_per_day.csv'), 'w');
fprintf(fid, 'day,e_out_fixed_wh,e_out_track_wh,e_motor_wh,gain_pct,gain_net_pct,completeness_pct\n');
for k = 1:numel(days)
    ds = datestr_day(days(k));
    fprintf('%-12s %10.1f %10.1f %9.2f %9.1f %9.1f %8.1f\n', ds, E(k, 1), E(k, 2), E(k, 3), gain(k), gain_net(k), E(k, 4));
    fprintf(fid, '%s,%.3f,%.3f,%.3f,%.2f,%.2f,%.1f\n', ds, E(k, 1), E(k, 2), E(k, 3), gain(k), gain_net(k), E(k, 4));
end
fclose(fid);
fprintf('%-12s %10.1f %10.1f %9.2f %9.1f %9.1f\n', 'TOTAL', sum(E(:, 1)), sum(E(:, 2)), sum(E(:, 3)), ...
        100 * (sum(E(:, 2)) / sum(E(:, 1)) - 1), 100 * ((sum(E(:, 2)) - sum(E(:, 3))) / sum(E(:, 1)) - 1));
R.energy = struct('days', days, 'E', E, 'gain', gain, 'gain_net', gain_net);

% ------------------------------------------------------------------ split by day
ntest = max(1, round(0.25 * numel(days)));
test_days = days(end - ntest + 1:end);
fprintf('\ntrain days: %d, test days: %d (%s ... %s)\n', numel(days) - ntest, ntest, ...
        datestr_day(test_days(1)), datestr_day(test_days(end)));

% ------------------------------------------------------------------ 2. forecast
H = 15;                                               % horizon [min]
FC = struct();
names = {'fixed', 'track'};
fid = fopen(fullfile(outdir, 'forecast_metrics.csv'), 'w');
fprintf(fid, 'channel,model,mae_w,rmse_w,nrmse_pct_of_50w,r2,skill_vs_persistence\n');
fprintf('\n15-min-ahead forecast of P_out (daylight test minutes)\n');
fprintf('%-6s %-18s %8s %8s %9s %7s %8s\n', 'ch', 'model', 'MAE[W]', 'RMSE[W]', 'nRMSE%', 'R2', 'skill');
for c = 1:2
    [X, y, base, isday, isTest, fnames] = forecast_set(D, c, H, test_days);
    tr = ~isTest; te = isTest & isday;
    % baselines
    yp_pers = base(te);
    A = [ones(sum(tr), 1), X(tr, :)];
    A(isnan(A)) = 0;
    w = A \ y(tr);
    At = [ones(sum(te), 1), X(te, :)]; At(isnan(At)) = 0;
    yp_lin = At * w;
    rf = rf_train(X(tr, :), y(tr), struct('ntrees', 60, 'max_depth', 12, 'min_leaf', 5, 'seed', c));
    yp_rf = rf_predict(rf, X(te, :));
    rmse_p = sqrt(mean((y(te) - yp_pers) .^ 2));
    models = {'persistence', yp_pers; 'linear regression', yp_lin; 'random forest', yp_rf};
    for k = 1:3
        e = y(te) - models{k, 2};
        mae = mean(abs(e)); rmse = sqrt(mean(e .^ 2));
        r2 = 1 - sum(e .^ 2) / sum((y(te) - mean(y(te))) .^ 2);
        fprintf('%-6s %-18s %8.3f %8.3f %9.2f %7.3f %8.3f\n', names{c}, models{k, 1}, mae, rmse, ...
                100 * rmse / 50, r2, 1 - rmse / rmse_p);
        fprintf(fid, '%s,%s,%.4f,%.4f,%.3f,%.4f,%.4f\n', names{c}, models{k, 1}, mae, rmse, 100 * rmse / 50, r2, 1 - rmse / rmse_p);
    end
    FC.(names{c}) = struct('y', y(te), 'pers', yp_pers, 'lin', yp_lin, 'rf', yp_rf, ...
                           't', D.t(D.ch == c), 'importance', rf.importance, 'features', {fnames});
    fprintf('       RF feature importance: %s\n', importance_text(fnames, rf.importance));
end
fclose(fid);
R.forecast = FC;

% ------------------------------------------------------------------ 3. classification
[Xc, lab, isTestC, cnames, fn_c] = class_set(D, test_days);
K = numel(cnames);
rfc = rf_train(Xc(~isTestC, :), lab(~isTestC), struct('task', 'classification', 'nclass', K, ...
               'ntrees', 80, 'max_depth', 14, 'min_leaf', 3, 'seed', 7, 'balanced', true));
pred = rf_predict(rfc, Xc(isTestC, :));
truth = lab(isTestC);
CM = accumarray([truth, pred], 1, [K K]);
fprintf('\ncondition classification (Random Forest), test days, daylight minutes: %d\n', numel(truth));
fprintf('%-24s %9s %9s %9s %9s\n', 'class', 'precision', 'recall', 'F1', 'support');
fid = fopen(fullfile(outdir, 'classification_report.csv'), 'w');
fprintf(fid, 'class,precision,recall,f1,support\n');
f1s = nan(K, 1);
for k = 1:K
    tp = CM(k, k); prc = tp / max(sum(CM(:, k)), 1); rec = tp / max(sum(CM(k, :)), 1);
    f1 = 2 * prc * rec / max(prc + rec, 1e-12);
    sup = sum(CM(k, :));
    if sup > 0, f1s(k) = f1; end
    fprintf('%-24s %9.3f %9.3f %9.3f %9d\n', cnames{k}, prc, rec, f1, sup);
    fprintf(fid, '%s,%.4f,%.4f,%.4f,%d\n', cnames{k}, prc, rec, f1, sup);
end
acc = trace(CM) / max(sum(CM(:)), 1);
fprintf('accuracy %.3f, macro-F1 %.3f (classes present in the test days)\n', acc, mean(f1s(~isnan(f1s))));
fprintf(fid, 'accuracy,%.4f,,,\nmacro_f1,%.4f,,,\n', acc, mean(f1s(~isnan(f1s))));
fclose(fid);
fprintf('RF feature importance: %s\n', importance_text(fn_c, rfc.importance));
write_matrix(fullfile(outdir, 'confusion_matrix.csv'), CM, cnames);
R.classification = struct('classes', {cnames}, 'confusion', CM, 'accuracy', acc, 'macro_f1', mean(f1s(~isnan(f1s))));

% ------------------------------------------------------------------ figures
try
    f = figure('Name', 'Energy and forecast', 'Position', [60 60 1100 750]);
    subplot(2, 2, 1);
    bar([E(:, 1), E(:, 2)]); grid on; legend('Fixed', 'Tracking', 'Location', 'northwest');
    xlabel('day #'); ylabel('E_{out} [Wh]'); title('Energy per day');
    subplot(2, 2, 2);
    bar(gain_net); grid on; xlabel('day #'); ylabel('%'); title('Net energy gain of the tracker');
    subplot(2, 1, 2);
    s = FC.track; n = min(numel(s.y), 600);
    plot(1:n, s.y(1:n), 'k', 1:n, s.pers(1:n), ':', 1:n, s.lin(1:n), '--', 1:n, s.rf(1:n), 'r'); grid on;
    legend('actual', 'persistence', 'linear', 'random forest', 'Location', 'northeast');
    xlabel('test minute (daylight, track channel)'); ylabel('P_{out}(t+15 min) [W]');
    title('15-minute-ahead forecast');
    save_png(f, fullfile(outdir, 'energy_forecast.png'));
    f2 = figure('Name', 'Confusion matrix', 'Position', [100 100 760 640]);
    imagesc(CM ./ max(sum(CM, 2), 1)); colorbar; axis image;
    set(gca, 'XTick', 1:K, 'XTickLabel', cnames, 'YTick', 1:K, 'YTickLabel', cnames);
    try, xtickangle(45); catch, end
    xlabel('predicted'); ylabel('true'); title('Condition classification (row-normalised)');
    for i = 1:K
        for j = 1:K
            if CM(i, j) > 0, text(j, i, sprintf('%d', CM(i, j)), 'HorizontalAlignment', 'center', 'Color', 'w'); end
        end
    end
    save_png(f2, fullfile(outdir, 'confusion_matrix.png'));
catch err
    fprintf('figures skipped: %s\n', err.message);
end
fprintf('\nresults written to %s\n', outdir);
end

% ======================================================================= data
function tf = is_url(s)
tf = numel(s) > 7 && (strncmpi(s, 'http://', 7) || strncmpi(s, 'https://', 8));
end

function D = load_api(base, start_local, stop_local, outdir)
url = sprintf('%s/api/export.csv?start=%s&stop=%s&m=all&every=1m&channel=all', ...
              regexprep(base, '/+$', ''), start_local, stop_local);
file = fullfile(outdir, 'pipeline_export_1m.csv');
fprintf('GET %s\n', url);
if exist('websave', 'file') || exist('websave', 'builtin')
    websave(file, url, weboptions('Timeout', 300));
else
    urlwrite(url, file);   % GNU Octave
end
[H, C] = read_csv(file);
col = @(name) get_col(H, C, name);
D.t = parse_time(col('time_utc'));
D.ch = 1 + strcmp(col('channel'), 'track');
D.label = col('label');
num = @(name) str2double_col(H, C, name);
D.p_out = num('p_out'); D.p_pv = num('p_pv'); D.v_pv = num('v_pv'); D.i_pv = num('i_pv');
D.v_out = num('v_out'); D.duty = num('duty'); D.t_panel = num('t_panel');
D.g = num('g'); D.t_amb = num('t_amb'); D.p_mot = num('p_mot');
D = share_environment(D);
end

function D = load_batch_folder(folder)
files = dir(fullfile(folder, 'day_*.csv'));
if isempty(files), files = dir(fullfile(folder, '*.csv')); end
if isempty(files), error('no CSV files in %s', folder); end
parts = {};
for k = 1:numel(files)
    [H, C] = read_csv(fullfile(folder, files(k).name));
    col = @(name) get_col(H, C, name);
    num = @(name) str2double_col(H, C, name);
    P.t = parse_time(col('timestamp'));
    P.ch = 1 + strcmp(col('panel_id'), 'tracker');
    P.label = col('fault_label');
    P.p_out = num('p_out'); P.p_pv = num('p_pv'); P.v_pv = num('v_pv'); P.i_pv = num('i_pv');
    P.v_out = num('v_out'); P.duty = num('duty'); P.t_panel = num('t_panel');
    gpoa = num('irradiance_poa'); P.g = gpoa; P.g(P.ch == 2) = NaN;     % ref cell sits on ESP32 #1
    P.t_amb = num('t_ambient'); P.t_amb(P.ch == 2) = NaN;
    P.p_mot = nan(size(P.t));
    P.dayid = k * ones(size(P.t));        % pvsim batch may draw the same date twice
    parts{end + 1} = P; %#ok<AGROW>
end
f = fieldnames(parts{1});
for i = 1:numel(f)
    D.(f{i}) = vertcat_field(parts, f{i});
end
D = share_environment(D);
end

function v = vertcat_field(parts, name)
v = [];
for k = 1:numel(parts), v = [v; parts{k}.(name)(:)]; end %#ok<AGROW>
end

function D = share_environment(D)
% the reference cell and air sensor are on ESP32 #1: give the same minute's values to track rows
key = round(D.t / 60);
if isfield(D, 'dayid'), key = key + 1e8 * D.dayid; end
fx = D.ch == 1 & ~isnan(D.g);
[kf, i] = unique(key(fx));
gf = D.g(fx); gf = gf(i);
af = D.t_amb(fx); af = af(i);
tr = find(D.ch == 2);
[ok, loc] = ismember(key(tr), kf);
D.g_env = D.g; D.t_env = D.t_amb;
D.g_env(tr(ok)) = gf(loc(ok));
D.t_env(tr(ok)) = af(loc(ok));
D.day = floor((D.t + 7 * 3600) / 86400);       % local (UTC+7) day number
if isfield(D, 'dayid'), D.day = D.day + 1e6 * D.dayid; end   % keep files with the same date apart
lt = mod(D.t + 7 * 3600, 86400) / 3600;          % local hour
D.hour = lt;
[D.sun_el, ~] = sun_elevation(D.t, 14.07, 100.60);
keep = ~isnan(D.t) & ~isnan(D.p_out);
f = fieldnames(D);
for i = 1:numel(f)
    if numel(D.(f{i})) == numel(keep), D.(f{i}) = D.(f{i})(keep); end
end
end

function [X, y, base, isday, isTest, names] = forecast_set(D, c, H, test_days)
m = find(D.ch == c);
[t, o] = sort(D.t(m)); m = m(o);
key = round(t / 60) + 1e8 * floor(D.day(m) / 1e6);
p = D.p_out(m);
lag = @(k) lookup(key, p, key - k);
y = lookup(key, p, key + H);
p5 = lag(5); p15 = lag(15);
el_now = D.sun_el(m);
el_fut = sun_elevation(t + H * 60, 14.07, 100.60);
X = [p, p5, p15, D.g_env(m), D.t_env(m), el_now, el_fut, sin(2 * pi * D.hour(m) / 24), cos(2 * pi * D.hour(m) / 24)];
names = {'p_out', 'p_out(t-5)', 'p_out(t-15)', 'g_refcell', 't_amb', 'sun_el', 'sun_el(t+15)', 'hour_sin', 'hour_cos'};
ok = ~isnan(y) & ~isnan(p5) & ~isnan(p15);
X = X(ok, :); y = y(ok); base = p(ok);
isday = el_fut(ok) > 0;
isTest = ismember(D.day(m(ok)), test_days);
end

function v = lookup(key, val, want)
[ok, loc] = ismember(want, key);
v = nan(size(want));
v(ok) = val(loc(ok));
end

function [X, lab, isTest, cnames, names] = class_set(D, test_days)
day = D.g_env > 50 & D.sun_el > 5;                       % daylight with enough light
g = D.g_env(day);
pr = D.p_pv(day) ./ (50 * g / 1000);                     % performance ratio of the 50 W panel
ir = D.i_pv(day) ./ (3.0 * g / 1000);                    % current ratio vs Isc(G)
tres = D.t_panel(day) - D.t_env(day) - (45 - 20) / 800 * g;   % NOCT residual
X = [pr, ir, D.v_pv(day), D.i_pv(day), D.p_pv(day), D.duty(day), D.v_out(day), tres, g, D.ch(day)];
names = {'perf_ratio', 'current_ratio', 'v_pv', 'i_pv', 'p_pv', 'duty', 'v_out', 't_residual', 'g_refcell', 'channel'};
labs = D.label(day);
[cnames, ~, lab] = unique(labs);
cnames = cnames(:)';
lab = lab(:);
isTest = ismember(D.day(day), test_days);
end

% ==================================================================== helpers
function [H, C] = read_csv(file)
txt = fileread(file);
if numel(txt) >= 3 && all(double(txt(1:3)) == [239 187 191]), txt = txt(4:end); end   % UTF-8 BOM
if ~isempty(txt) && double(txt(1)) == 65279, txt = txt(2:end); end
nl = find(txt == 10, 1);
H = strsplit(strtrim(txt(1:nl - 1)), ',');
fmt = repmat('%s', 1, numel(H));
C = textscan(txt(nl + 1:end), fmt, 'Delimiter', ',', 'EndOfLine', '\n', 'ReturnOnError', false);
end

function v = get_col(H, C, name)
k = find(strcmp(H, name), 1);
if isempty(k), error('column %s not found (have: %s)', name, strjoin(H, ', ')); end
v = C{k};
end

function v = str2double_col(H, C, name)
k = find(strcmp(H, name), 1);
n = numel(C{1});
if isempty(k), v = nan(n, 1); return; end
v = str2double(C{k});
v = v(:);
if numel(v) < n, v(end + 1:n) = NaN; end
end

function t = parse_time(s)
% ISO 8601 -> seconds since 1970 UTC ("...Z" or "...+07:00")
c = char(s);
y = num(c(:, 1:4)); mo = num(c(:, 6:7)); d = num(c(:, 9:10));
h = num(c(:, 12:13)); mi = num(c(:, 15:16)); se = num(c(:, 18:19));
dn = datenum(y, mo, d, h, mi, se);
t = (dn - 719529) * 86400;
off = zeros(size(t));
w = size(c, 2);
for k = 1:size(c, 1)
    r = strtrim(c(k, :));
    m = regexp(r, '([+-])(\d\d):(\d\d)$', 'tokens', 'once');
    if ~isempty(m)
        sg = 1; if m{1} == '-', sg = -1; end
        off(k) = sg * (str2double(m{2}) * 3600 + str2double(m{3}) * 60);
    end
end
t = round(t - off);
end

function v = num(c)
v = (double(c) - 48) * (10 .^ (size(c, 2) - 1:-1:0))';
end

function [el, az] = sun_elevation(t_utc, lat, lon)
% NOAA solar position (the equations of pvsim/environment/solar_position.py), vectorised
D = pi / 180;
jd = t_utc / 86400 + 2440587.5; jc = (jd - 2451545) / 36525;
L0 = mod(280.46646 + jc .* (36000.76983 + 0.0003032 * jc), 360);
M = 357.52911 + jc .* (35999.05029 - 0.0001537 * jc);
e = 0.016708634 - jc .* (0.000042037 + 1.267e-7 * jc);
C = sin(M * D) .* (1.914602 - jc .* (0.004817 + 0.000014 * jc)) + sin(2 * M * D) .* (0.019993 - 0.000101 * jc) + sin(3 * M * D) * 0.000289;
om = 125.04 - 1934.136 * jc;
lam = L0 + C - 0.00569 - 0.00478 * sin(om * D);
eps0 = 23 + (26 + (21.448 - jc .* (46.815 + jc .* (0.00059 - jc * 0.001813))) / 60) / 60;
ep = eps0 + 0.00256 * cos(om * D);
decl = asin(sin(ep * D) .* sin(lam * D));
yv = tan(ep * D / 2) .^ 2;
eot = 4 / D * (yv .* sin(2 * L0 * D) - 2 * e .* sin(M * D) + 4 * e .* yv .* sin(M * D) .* cos(2 * L0 * D) ...
      - 0.5 * yv .^ 2 .* sin(4 * L0 * D) - 1.25 * e .^ 2 .* sin(2 * M * D));
tst = mod(mod(t_utc, 86400) / 60 + eot + 4 * lon, 1440);
ha = tst / 4 - 180;
cz = sin(lat * D) * sin(decl) + cos(lat * D) * cos(decl) .* cos(ha * D);
zen = acos(min(max(cz, -1), 1));
el = 90 - zen / D;
az = zeros(size(el));
end

function s = datestr_day(day_number)
dn = mod(day_number, 1e6) + 719529;
v = datevec(dn);
s = sprintf('%04d-%02d-%02d', v(1), v(2), v(3));
end

function s = importance_text(names, imp)
[v, o] = sort(imp, 'descend');
k = min(5, numel(o));
parts = cell(1, k);
for i = 1:k, parts{i} = sprintf('%s %.2f', names{o(i)}, v(i)); end
s = strjoin(parts, ', ');
end

function write_matrix(file, CM, names)
fid = fopen(file, 'w');
fprintf(fid, 'true\\predicted,%s\n', strjoin(names, ','));
for i = 1:size(CM, 1)
    fprintf(fid, '%s%s\n', names{i}, sprintf(',%d', CM(i, :)));
end
fclose(fid);
end

function save_png(fig, path)
try
    if exist('exportgraphics', 'file') || exist('exportgraphics', 'builtin')
        exportgraphics(fig, path, 'Resolution', 150);
    else
        print(fig, path, '-dpng', '-r150');
    end
catch err
    fprintf('could not save %s (%s)\n', path, err.message);
end
end
