function model = rf_train(X, y, opts)
%RF_TRAIN  Random Forest (regression or classification) in plain MATLAB, no toolbox.
%
%   model = rf_train(X, y, opts)
%     X     n-by-p numeric features (NaN is replaced by the column median)
%     y     n-by-1 target: real values (regression) or class ids 1..K (classification)
%     opts  struct, all fields optional:
%           task       'regression' (default) or 'classification'
%           ntrees     number of trees (default 60)
%           max_depth  default 12
%           min_leaf   minimum samples per leaf (default 5)
%           mtry       features tried per split (default p/3 regression, sqrt(p) classification)
%           nbins      candidate thresholds per feature, from quantiles (default 32)
%           nclass     K for classification (default max(y))
%           seed       random seed (default 1)
%           balanced   classification: draw the same number of bootstrap samples from every
%                      class (balanced random forest, Chen, Liaw & Breiman 2004) so rare
%                      fault classes are not drowned by "normal" (default false)
%
%   Breiman (2001) Random Forests: bootstrap sample per tree, random feature subset per
%   split, CART splits (variance reduction / Gini).  Thresholds are restricted to feature
%   quantiles (histogram splitting, as in LightGBM / scikit-learn HistGradientBoosting)
%   so training stays fast without the Statistics and Machine Learning Toolbox.
%   Predict with rf_predict(model, X).

if nargin < 3, opts = struct(); end
[n, p] = size(X);
task = getopt(opts, 'task', 'regression');
is_cls = strcmp(task, 'classification');
ntrees = getopt(opts, 'ntrees', 60);
max_depth = getopt(opts, 'max_depth', 12);
min_leaf = getopt(opts, 'min_leaf', 5);
if is_cls
    mtry = getopt(opts, 'mtry', max(1, round(sqrt(p))));
    K = getopt(opts, 'nclass', max(y));
else
    mtry = getopt(opts, 'mtry', max(1, round(p / 3)));
    K = 1;
end
nbins = getopt(opts, 'nbins', 32);
set_seed(getopt(opts, 'seed', 1));

% ---- fill NaN, bin every feature once at its quantiles
med = zeros(1, p);
for j = 1:p
    c = X(:, j);
    med(j) = median(c(~isnan(c)));
    if isnan(med(j)), med(j) = 0; end
    c(isnan(c)) = med(j);
    X(:, j) = c;
end
edges = cell(1, p);
B = zeros(n, p, 'uint16');
for j = 1:p
    s = sort(X(:, j));
    q = unique(s(max(1, min(n, round((1:nbins - 1) / nbins * n)))));
    edges{j} = q(:)';
    B(:, j) = bin_index(X(:, j), edges{j});
end

model = struct('task', task, 'K', K, 'p', p, 'median', med, 'trees', {cell(1, ntrees)});
balanced = is_cls && getopt(opts, 'balanced', false);
if balanced
    members = cell(1, K);
    for k = 1:K, members{k} = find(y == k); end
    members = members(~cellfun(@isempty, members));
    per = ceil(n / numel(members));
end
for t = 1:ntrees
    if balanced                                              % class-balanced bootstrap
        idx = zeros(per * numel(members), 1);
        for k = 1:numel(members)
            mk = members{k};
            idx((k - 1) * per + (1:per)) = mk(randi(numel(mk), per, 1));
        end
    else
        idx = randi(n, n, 1);                                % bootstrap sample
    end
    model.trees{t} = grow_tree(B(idx, :), y(idx), edges, is_cls, K, max_depth, min_leaf, mtry);
end
model.importance = zeros(1, p);                              % total impurity decrease
for t = 1:ntrees
    tr = model.trees{t};
    for k = find(tr.feat > 0)
        model.importance(tr.feat(k)) = model.importance(tr.feat(k)) + tr.gain(k);
    end
end
if sum(model.importance) > 0
    model.importance = model.importance / sum(model.importance);
end
end

% -------------------------------------------------------------------------
function tr = grow_tree(B, y, edges, is_cls, K, max_depth, min_leaf, mtry)
p = size(B, 2);
cap = 4096;
feat = zeros(1, cap); thr = zeros(1, cap); left = zeros(1, cap); right = zeros(1, cap);
gain = zeros(1, cap);
if is_cls, val = zeros(cap, K); else, val = zeros(cap, 1); end
stack_idx = {(1:numel(y))'}; stack_node = 1; stack_depth = 0;
nnodes = 1;
while ~isempty(stack_node)
    ids = stack_idx{end}; node = stack_node(end); depth = stack_depth(end);
    stack_idx(end) = []; stack_node(end) = []; stack_depth(end) = [];
    yy = y(ids); m = numel(ids);
    if is_cls
        cnt = accumarray(yy, 1, [K 1])';
        val(node, :) = cnt / m;
        pure = max(cnt) == m;
    else
        val(node) = mean(yy);
        pure = all(yy == yy(1));
    end
    if depth >= max_depth || m < 2 * min_leaf || pure
        continue;
    end
    best_score = -inf; best_j = 0; best_k = 0;
    if is_cls
        base = sum(cnt .^ 2) / m;
    else
        S = sum(yy);
        base = S ^ 2 / m;
    end
    for j = randperm(p, mtry)
        b = double(B(ids, j));
        nb = numel(edges{j}) + 1;
        if nb < 2, continue; end
        cL = cumsum(accumarray(b, 1, [nb 1]));
        cR = m - cL;
        if is_cls
            C = accumarray([b, yy], 1, [nb K]);
            CL = cumsum(C, 1);
            CR = repmat(sum(C, 1), nb, 1) - CL;
            score = sum(CL .^ 2, 2) ./ max(cL, 1) + sum(CR .^ 2, 2) ./ max(cR, 1);
        else
            sL = cumsum(accumarray(b, yy, [nb 1]));
            sR = S - sL;
            score = sL .^ 2 ./ max(cL, 1) + sR .^ 2 ./ max(cR, 1);
        end
        ok = cL >= min_leaf & cR >= min_leaf;
        ok(end) = false;
        score(~ok) = -inf;
        [sc, k] = max(score);
        if sc > best_score
            best_score = sc; best_j = j; best_k = k;
        end
    end
    if best_j == 0 || best_score <= base + 1e-12
        continue;                                             % no useful split: leaf
    end
    if nnodes + 2 > cap                                       % grow storage
        cap = 2 * cap;
        feat(cap) = 0; thr(cap) = 0; left(cap) = 0; right(cap) = 0; gain(cap) = 0;
        val(cap, end) = 0;
    end
    goes_left = B(ids, best_j) <= best_k;
    feat(node) = best_j;
    thr(node) = edges{best_j}(best_k);                        % x <= thr goes left
    gain(node) = best_score - base;
    l = nnodes + 1; r = nnodes + 2; nnodes = nnodes + 2;
    left(node) = l; right(node) = r;
    stack_idx{end + 1} = ids(goes_left);  stack_node(end + 1) = l; stack_depth(end + 1) = depth + 1; %#ok<AGROW>
    stack_idx{end + 1} = ids(~goes_left); stack_node(end + 1) = r; stack_depth(end + 1) = depth + 1; %#ok<AGROW>
end
tr.feat = feat(1:nnodes); tr.thr = thr(1:nnodes); tr.left = left(1:nnodes);
tr.right = right(1:nnodes); tr.gain = gain(1:nnodes); tr.val = val(1:nnodes, :);
end

function b = bin_index(x, q)
% bin k means q(k-1) < x <= q(k); values above the last edge go to numel(q)+1
b = ones(size(x));
for k = 1:numel(q)
    b = b + (x > q(k));
end
end

function v = getopt(s, name, default)
if isfield(s, name) && ~isempty(s.(name)), v = s.(name); else, v = default; end
end

function set_seed(seed)
try
    rng(seed);
catch
    rand('state', seed); randn('state', seed); %#ok<RAND>
end
end
