function [yhat, prob] = rf_predict(model, X)
%RF_PREDICT  Predict with a forest from rf_train.
%   Regression:     yhat = mean of the trees.
%   Classification: prob = mean class probabilities (n-by-K), yhat = most probable class id.

n = size(X, 1);
for j = 1:model.p
    c = X(:, j);
    c(isnan(c)) = model.median(j);
    X(:, j) = c;
end
T = numel(model.trees);
if strcmp(model.task, 'classification')
    prob = zeros(n, model.K);
else
    prob = zeros(n, 1);
end
rows = (1:n)';
for t = 1:T
    tr = model.trees{t};
    nd = ones(n, 1);
    active = tr.feat(nd)' > 0;
    while any(active)
        a = find(active);
        f = tr.feat(nd(a))';
        x = X(sub2ind(size(X), rows(a), f));
        goleft = x <= tr.thr(nd(a))';
        nxt = tr.right(nd(a))';
        nxt(goleft) = tr.left(nd(a(goleft)))';
        nd(a) = nxt;
        active(a) = tr.feat(nd(a))' > 0;
    end
    prob = prob + tr.val(nd, :);
end
prob = prob / T;
if strcmp(model.task, 'classification')
    [~, yhat] = max(prob, [], 2);
else
    yhat = prob;
end
end
