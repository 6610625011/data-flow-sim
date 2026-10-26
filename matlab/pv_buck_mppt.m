function results = pv_buck_mppt()
%PV_BUCK_MPPT  50 W PV module + buck converter + MPPT (P&O vs Incremental Conductance).
%
%   Plain MATLAB (no toolbox, runs in R2026b and in GNU Octave).  Run it from this folder:
%       >> results = pv_buck_mppt
%
%   What it does (scope item 4 / objective 2 of the proposal):
%     1. Same 50 W module as pvsim: single-diode model with the parameters pvsim extracts
%        from the datasheet (Villalva 2009 / De Soto 2006 translation to G and T).
%     2. Buck converter, averaged CCM model with the same loss terms and component values
%        as pvsim/converter/buck_averaged.py, CR electronic load 3 ohm.
%     3. MPPT: P&O and Incremental Conductance ported line by line from the firmware
%        (firmware_ref/mppt.c): fixed step 0.005, period 0.1 s, start at D = 0.3.
%     4. One irradiance test profile for both algorithms (steps 1000->500->1000 W/m2,
%        ramp 1000->300 W/m2) and the metrics of scope item 4: tracked power (eta_MPPT),
%        oscillation around the MPP, response time.
%     5. Cross-check: the same profile simulated by pvsim (pvsim_reference_mppt.csv,
%        made with pvsim.engine.live.LiveChannel) is overlaid and compared.
%     6. Switching-level check of the buck at 40 kHz (inductor-current and output-voltage
%        ripple) against the textbook ripple formula used by pvsim.
%
%   Outputs: figures (PNG in ./out), out/mppt_metrics.csv, and the struct `results`.

here = fileparts(mfilename('fullpath'));
outdir = fullfile(here, 'out');
if ~exist(outdir, 'dir'), mkdir(outdir); end

pv = pv_params();
bk = buck_params();
R_load = 3.0;                    % electronic load, CR mode [ohm] (pvsim live channel)
T_amb = 30.0;                    % [degC]
dt = 0.1;                        % MPPT period [s]
t = (0:999)' * dt;               % 100 s
G = arrayfun(@irradiance_profile, t);

algos = {'po', 'inc'};
names = {'P&O', 'Incremental Conductance'};
sim = struct();
for a = 1:2
    sim.(algos{a}) = run_mppt(algos{a}, t, G, T_amb, pv, bk, R_load);
end
ref = read_reference(fullfile(here, 'pvsim_reference_mppt.csv'));

% ------------------------------------------------------------------ metrics
windows = [15 20; 35 40; 55 60; 95 100];      % steady parts of the profile [s]
steps = [20 40];                               % irradiance steps [s]
fprintf('\n%-26s %-9s %8s %8s %10s %10s %10s %10s\n', 'algorithm', 'model', 'eta_all', ...
        'eta_ss', 'osc_P[%]', 'std_D', 't_resp20', 't_resp40');
rows = {};
for a = 1:2
    for src = 1:2
        if src == 1
            s = sim.(algos{a}); model = 'MATLAB';
        else
            if isempty(ref), continue; end
            s = ref.(algos{a}); model = 'pvsim';
        end
        m = mppt_metrics(t, s.p_pv, s.p_mpp, s.duty, windows, steps);
        fprintf('%-26s %-9s %8.4f %8.4f %10.3f %10.4f %10.1f %10.1f\n', names{a}, model, ...
                m.eta_all, m.eta_ss, m.osc_pct, m.std_d, m.t_resp(1), m.t_resp(2));
        rows(end + 1, :) = {names{a}, model, m.eta_all, m.eta_ss, m.osc_pct, m.std_d, ...
                            m.t_resp(1), m.t_resp(2)}; %#ok<AGROW>
        results.(algos{a}).(lower(model)) = m;
    end
end
fid = fopen(fullfile(outdir, 'mppt_metrics.csv'), 'w');
fprintf(fid, 'algorithm,model,eta_all,eta_steady,oscillation_pct,std_duty,t_resp_step20_s,t_resp_step40_s\n');
for k = 1:size(rows, 1)
    fprintf(fid, '%s,%s,%.5f,%.5f,%.4f,%.5f,%.1f,%.1f\n', rows{k, :});
end
fclose(fid);
fprintf(['eta_all = integral(P_pv)/integral(P_mpp) over 100 s (EN 50530 definition)\n' ...
         'eta_ss / osc_P = in the steady windows 15-20, 35-40, 55-60, 95-100 s\n' ...
         't_resp = time after the step until P_pv stays >= 98 %% of P_mpp for 1 s\n']);

% ------------------------------------------------------------------ figures
f1 = figure('Name', 'MPPT P&O vs IncCond', 'Position', [80 80 1000 700]);
subplot(3, 1, 1);
plot(t, G, 'k', 'LineWidth', 1.2); grid on; ylabel('G [W/m^2]');
title('Irradiance profile (T_{amb} = 30 \circC, cell temperature from NOCT)');
subplot(3, 1, 2); hold on; grid on;
plot(t, sim.po.p_mpp, 'k:', 'LineWidth', 1.5);
plot(t, sim.po.p_pv, 'b', t, sim.inc.p_pv, 'r');
leg = {'P_{mpp} (true)', 'P&O (MATLAB)', 'IncCond (MATLAB)'};
if ~isempty(ref)
    plot(t, ref.po.p_pv, 'b--', t, ref.inc.p_pv, 'r--');
    leg = [leg, {'P&O (pvsim)', 'IncCond (pvsim)'}];
end
ylabel('P_{pv} [W]'); legend(leg, 'Location', 'southwest');
subplot(3, 1, 3); hold on; grid on;
plot(t, sim.po.duty, 'b', t, sim.inc.duty, 'r');
if ~isempty(ref), plot(t, ref.po.duty, 'b--', t, ref.inc.duty, 'r--'); end
ylabel('duty D'); xlabel('t [s]');
save_png(f1, fullfile(outdir, 'mppt_compare.png'));

sw = switching_check(bk, R_load);
f2 = figure('Name', 'Buck switching ripple', 'Position', [120 120 1000 520]);
subplot(2, 1, 1);
plot(sw.t * 1e3, sw.il, 'b'); grid on; ylabel('i_L [A]'); xlim([0 0.25]);
title(sprintf('Buck at D = %.2f, V_{in} = %.1f V, 40 kHz: \\Deltai_L = %.3f A (formula %.3f A)', ...
              sw.D, sw.vin, sw.dil_sim, sw.dil_formula));
subplot(2, 1, 2);
plot(sw.t * 1e3, sw.vo, 'r'); grid on; ylabel('v_{out} [V]'); xlabel('t [ms]'); xlim([0 0.25]);
title(sprintf('\\Deltav_{out} = %.1f mV (formula %.1f mV), V_{out} = %.2f V', ...
              1e3 * sw.dvo_sim, 1e3 * sw.dvo_formula, sw.vo_mean));
save_png(f2, fullfile(outdir, 'buck_ripple.png'));
fprintf(['\nBuck switching check: dI_L = %.3f A (simulated) vs %.3f A (formula), ' ...
         'dV_out = %.2f mV vs %.2f mV, V_out = %.2f V vs averaged model %.2f V\n'], ...
        sw.dil_sim, sw.dil_formula, 1e3 * sw.dvo_sim, 1e3 * sw.dvo_formula, sw.vo_mean, sw.vo_avg_model);
results.switching = sw;
results.sim = sim;
fprintf('Figures and mppt_metrics.csv written to %s\n', outdir);
end

% =================================================================== model
function pv = pv_params()
% STC parameters that pvsim extracts from the 50 W datasheet (pvsim/pv/datasheet.py,
% printed with Panel(PanelDatasheet()).ref) and the datasheet coefficients.
pv.iph_ref = 3.00055532896716;   % [A]
pv.rs = 0.09884733781558779;     % [ohm]
pv.rsh_ref = 534.0059894172839;  % [ohm]
pv.a_ref = 1.202412702866818;    % n*Ns*k*T/q at 25 degC [V]
pv.voc = 21.6;                   % [V]
pv.alpha_isc = 0.05 / 100 * 3.0; % [A/K]
pv.beta_voc = -0.32 / 100 * 21.6;% [V/K]
pv.noct = 45.0;                  % [degC]
end

function bk = buck_params()
% pvsim/converter/buck_averaged.py BuckParams defaults (asynchronous buck)
bk.l = 220e-6; bk.r_l = 0.05; bk.c_out = 470e-6; bk.esr_out = 0.05; bk.esr_in = 0.05;
bk.rds_on = 0.03; bk.v_f = 0.45; bk.f_sw = 40e3; bk.t_r = 40e-9; bk.t_f = 40e-9;
bk.q_g = 20e-9; bk.v_g = 10.0; bk.d_min = 0.0; bk.d_max = 0.95; bk.v_uvlo = 5.0;
end

function g = irradiance_profile(t)
if t < 20
    g = 1000;
elseif t < 40
    g = 500;
elseif t < 60
    g = 1000;
elseif t < 80
    g = 1000 - 700 * (t - 60) / 20;
else
    g = 300;
end
g = round(g * 10) / 10;
end

function p = params_at(pv, g, t_cell)
% De Soto (2006) / Villalva (2009) translation, as pvsim params_at()
T = t_cell + 273.15; dT = T - 298.15;
p.a = pv.a_ref * T / 298.15;
iph_full = pv.iph_ref + pv.alpha_isc * dT;
voc_t = pv.voc + pv.beta_voc * dT;
p.i0 = (iph_full - voc_t / pv.rsh_ref) / expm1(voc_t / p.a);
g = max(g, 0);
p.iph = iph_full * g / 1000;
p.rsh = pv.rsh_ref * 1000 / max(g, 1);
p.rs = pv.rs;
p.voc = voc_t;
end

function i = pv_current(v, p)
% single-diode current at voltage v (vectorised Newton on I)
i = p.iph * ones(size(v));
for k = 1:60
    x = (v + i * p.rs) / p.a;
    f = p.iph - p.i0 * expm1(x) - (v + i * p.rs) / p.rsh - i;
    df = -p.i0 * exp(x) * p.rs / p.a - p.rs / p.rsh - 1;
    step = f ./ df;
    i = i - step;
    if max(abs(step)) < 1e-12, break; end
end
end

function [i_in, v_out, i_out, p_out] = buck_ccm(v_in, d, bk, R)
% averaged CCM buck with the loss terms of pvsim (CR load); i_in from the power balance
d = min(max(d, bk.d_min), bk.d_max);
if v_in < bk.v_uvlo || d <= 0
    i_in = 0; v_out = 0; i_out = 0; p_out = 0; return;
end
e = d * v_in - (1 - d) * bk.v_f;
req = d * bk.rds_on + bk.r_l;
v_out = max(e, 0) * R / (R + req);
i_out = v_out / R;
d_il = max(v_in - v_out - i_out * req, 0) * d / (bk.l * bk.f_sw);
il2 = i_out^2 + d_il^2 / 12;
loss = d * il2 * bk.rds_on + il2 * bk.r_l + d_il^2 / 12 * bk.esr_out ...
     + max(d * il2 - (d * i_out)^2, 0) * bk.esr_in + 0.5 * v_in * i_out * (bk.t_r + bk.t_f) * bk.f_sw ...
     + bk.q_g * bk.v_g * bk.f_sw + (1 - d) * i_out * bk.v_f;
p_out = v_out * i_out;
i_in = (p_out + loss) / v_in;
end

function [v, i] = operating_point(p, d, bk, R, v_prev)
% intersection of the PV curve with the converter input characteristic (pvsim method)
f = @(v) pv_current(v, p) - buck_ccm(v, d, bk, R);
vg = linspace(bk.v_uvlo, max(p.voc, bk.v_uvlo + 0.1), 120);
fv = arrayfun(f, vg);
idx = find(sign(fv(1:end - 1)) ~= sign(fv(2:end)));
if isempty(idx)          % input collapsed below UVLO or converter off: open circuit
    v = fzero(@(v) pv_current(v, p), [0, p.voc + 2]); i = 0; return;
end
if ~isempty(v_prev)       % warm start: the root nearest the previous operating point
    [~, k] = min(abs(vg(idx) - v_prev)); idx = idx(k);
else
    idx = idx(end);       % highest root
end
v = fzero(f, [vg(idx), vg(idx + 1)]);
i = pv_current(v, p);
end

function pmpp = true_mpp(p)
v = linspace(0, p.voc, 400);
pw = v .* pv_current(v, p);
[~, k] = max(pw);
lo = v(max(k - 1, 1)); hi = v(min(k + 1, numel(v)));
vv = linspace(lo, hi, 200);
pmpp = max(vv .* pv_current(vv, p));
end

% =================================================================== MPPT
function m = mppt_new(algo)
% mppt.c defaults with d_init = 0.3 (as the firmware and esp32-sim)
m.algo = algo; m.step = 0.005; m.d_min = 0.0; m.d_max = 0.95;
m.eps_v = 1e-6; m.eps_i = 1e-6; m.eps_g = 0.0;
m.d = 0.3; m.dir = 1; m.n = 0; m.v_prev = 0; m.i_prev = 0; m.p_prev = 0;
end

function m = mppt_step(m, v, i)
% port of po_step() and inccond_step() in firmware_ref/mppt.c
clip = @(d) min(max(d, m.d_min), m.d_max);
pw = v * i;
if strcmp(m.algo, 'po')
    if m.n > 0
        if pw - m.p_prev < 0, m.dir = -m.dir; end
    end
    m.d = clip(m.d + m.dir * m.step);
else
    if v <= m.eps_v                      % input collapsed: back off the load
        m.d = clip(m.d - 4 * m.step);
    elseif m.n == 0
        m.d = clip(m.d + m.step);
    else
        dv = v - m.v_prev; di = i - m.i_prev; direction = 0;   % +1 raise V, -1 lower V
        if abs(dv) < m.eps_v
            if di > m.eps_i, direction = 1; elseif di < -m.eps_i, direction = -1; end
        else
            g = di / dv + i / v;
            if g > m.eps_g, direction = 1; elseif g < -m.eps_g, direction = -1; end
        end
        if direction ~= 0, m.d = clip(m.d - direction * m.step); end   % buck: higher D = lower V
    end
end
m.v_prev = v; m.i_prev = i; m.p_prev = pw; m.n = m.n + 1;
end

function s = run_mppt(algo, t, G, T_amb, pv, bk, R)
n = numel(t);
s.duty = zeros(n, 1); s.v_pv = zeros(n, 1); s.i_pv = zeros(n, 1);
s.p_pv = zeros(n, 1); s.p_mpp = zeros(n, 1); s.p_out = zeros(n, 1);
m = mppt_new(algo);
v_prev = [];
cache_g = NaN; p = []; pmpp = 0;
for k = 1:n
    if G(k) ~= cache_g
        t_cell = T_amb + (pv.noct - 20) / 800 * G(k);    % NOCT model (pvsim)
        p = params_at(pv, G(k), t_cell);
        pmpp = true_mpp(p);
        cache_g = G(k);
    end
    d = m.d;                               % duty applied during this period
    [v, i] = operating_point(p, d, bk, R, v_prev);
    v_prev = v;
    [~, ~, ~, po] = buck_ccm(v, d, bk, R);
    s.duty(k) = d; s.v_pv(k) = v; s.i_pv(k) = i; s.p_pv(k) = v * i;
    s.p_mpp(k) = pmpp; s.p_out(k) = po;
    m = mppt_step(m, v, i);                % next duty
end
end

function m = mppt_metrics(t, p_pv, p_mpp, duty, windows, steps)
m.eta_all = trapz(t, p_pv) / trapz(t, p_mpp);
in = false(size(t));
for w = 1:size(windows, 1)
    in = in | (t >= windows(w, 1) & t < windows(w, 2));
end
m.eta_ss = sum(p_pv(in)) / sum(p_mpp(in));
m.osc_pct = 100 * mean((p_mpp(in) - p_pv(in)) ./ p_mpp(in));
sd = zeros(size(windows, 1), 1);
for w = 1:size(windows, 1)
    k = t >= windows(w, 1) & t < windows(w, 2);
    sd(w) = std(duty(k));
end
m.std_d = mean(sd);
m.t_resp = nan(1, numel(steps));
dt = t(2) - t(1);
hold_n = round(1 / dt);
for s = 1:numel(steps)
    k0 = find(t >= steps(s), 1);
    ok = p_pv >= 0.98 * p_mpp;
    for k = k0:numel(t) - hold_n
        if all(ok(k:k + hold_n))
            m.t_resp(s) = t(k) - steps(s);
            break;
        end
    end
end
end

% =========================================================== switching check
function sw = switching_check(bk, R)
% Ideal-switch buck at 40 kHz, fed from a stiff 16 V source (the PV MPP voltage at
% 1000 W/m2 and ~61 degC), D chosen near the MPP duty.  The state starts at the averaged
% operating point (so the slow L-C resonance, ~490 Hz, is not excited) and is integrated
% with explicit Euler, 400 steps per switching period, for 10 ms.  Ripple = mean
% peak-to-peak per switching period over the last 20 periods.
vin = 16.0; D = 0.72;
[~, vo_avg, io_avg] = buck_ccm(vin, D, bk, R);
Ts = 1 / bk.f_sw; spp = 400; h = Ts / spp; n = round(10e-3 / h);
il = io_avg; vc = vo_avg;
T = zeros(n, 1); IL = zeros(n, 1); VO = zeros(n, 1);
for k = 1:n
    tk = (k - 1) * h;
    on = mod(k - 1, spp) < D * spp;
    vo = (vc + bk.esr_out * il) / (1 + bk.esr_out / R);   % output node with capacitor ESR
    if on
        dil = (vin - il * (bk.rds_on + bk.r_l) - vo) / bk.l;
    elseif il > 0
        dil = (-bk.v_f - il * bk.r_l - vo) / bk.l;
    else
        dil = 0;  % DCM: diode blocks
    end
    ic = il - vo / R;
    il = max(il + h * dil, 0);
    vc = vc + h * ic / bk.c_out;
    T(k) = tk; IL(k) = il; VO(k) = vo;
end
nper = 20;
last = (n - nper * spp + 1):n;
P_il = reshape(IL(last), spp, nper); P_vo = reshape(VO(last), spp, nper);
sw.t = T(last) - T(last(1)); sw.il = IL(last); sw.vo = VO(last);
sw.D = D; sw.vin = vin;
sw.dil_sim = mean(max(P_il) - min(P_il));
sw.dvo_sim = mean(max(P_vo) - min(P_vo));
sw.vo_mean = mean(sw.vo);
sw.vo_avg_model = vo_avg;
req = D * bk.rds_on + bk.r_l;
sw.dil_formula = max(vin - vo_avg - io_avg * req, 0) * D / (bk.l * bk.f_sw);   % pvsim / Erickson eq. 2.15
% output ripple: capacitor charge dQ = dI*Ts/8 (Erickson sec. 2.5) plus the ESR term
sw.dvo_formula = sw.dil_formula / (8 * bk.f_sw * bk.c_out) + sw.dil_formula * bk.esr_out * R / (R + bk.esr_out);
end

% ================================================================== helpers
function ref = read_reference(path)
ref = [];
if ~exist(path, 'file')
    fprintf('pvsim reference %s not found: MATLAB results only\n', path);
    return;
end
fid = fopen(path, 'r');
c = textscan(fid, '%s %f %f %f %f %f %f %f %f %f', 'Delimiter', ',', 'HeaderLines', 1);
fclose(fid);
algo = c{1};
for a = {'po', 'inc'}
    k = strcmp(algo, a{1});
    ref.(a{1}) = struct('t', c{2}(k), 'g', c{3}(k), 'duty', c{4}(k), 'v_pv', c{5}(k), ...
                        'i_pv', c{6}(k), 'p_pv', c{7}(k), 'p_mpp', c{8}(k), 'p_out', c{9}(k));
end
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
