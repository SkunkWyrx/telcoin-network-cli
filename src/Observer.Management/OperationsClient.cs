using System.Net.Http.Headers;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

class OperationsClient {
    readonly IConfiguration config;
    readonly HttpClient client;
    readonly string repo;
    const string Workflow = "observer-management-operations.yml";
    public string HistoryUrl => $"https://github.com/{repo}/actions/workflows/{Workflow}";

    public OperationsClient(IConfiguration config, HttpClient client) {
        this.config = config;
        this.client = client;
        repo = config["Operations:Repository"] ?? throw new InvalidOperationException("Missing repository");
        if (!Regex.IsMatch(repo, @"\A[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\z"))
            throw new InvalidOperationException("Invalid repository");
        if (config["Fleet:Network"] is not ("adiri" or "mainnet"))
            throw new InvalidOperationException("Invalid network");
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", config["Operations:GitHubToken"]);
        client.DefaultRequestHeaders.UserAgent.ParseAdd("Oprail-Observer-Management/1.0");
        client.DefaultRequestHeaders.Accept.ParseAdd("application/vnd.github+json");
    }

    public async Task Dispatch(Operation operation, string requestId, string actor) {
        using var response = await client.PostAsJsonAsync($"https://api.github.com/repos/{repo}/actions/workflows/{Workflow}/dispatches", new {
            @ref = "main", inputs = new { network = config["Fleet:Network"], operation = operation.Action,
                argument = operation.Argument, requested_by = actor, request_id = requestId }
        });
        response.EnsureSuccessStatusCode();
    }

    public async Task<object[]> History() {
        var data = await client.GetFromJsonAsync<JsonObject>($"https://api.github.com/repos/{repo}/actions/workflows/{Workflow}/runs?branch=main&event=workflow_dispatch&per_page=50");
        var prefix = config["Fleet:Network"] + " / ";
        var selected = data!["workflow_runs"]!.AsArray().Where(r =>
            r!["display_title"]!.GetValue<string>().StartsWith(prefix, StringComparison.Ordinal)).Take(15).ToArray();
        return await Task.WhenAll(selected.Select(async r => {
            var id = r!["id"]!.GetValue<long>();
            var state = r["status"]!.GetValue<string>();
            string? step = null;
            if (state == "in_progress") {
                try {
                    var jobs = await client.GetFromJsonAsync<JsonObject>($"https://api.github.com/repos/{repo}/actions/runs/{id}/jobs");
                    step = jobs!["jobs"]!.AsArray().SelectMany(j => j!["steps"]!.AsArray())
                        .FirstOrDefault(s => s!["status"]!.GetValue<string>() == "in_progress")?["name"]?.GetValue<string>();
                } catch (HttpRequestException) { step = "Waiting for runner status"; }
            }
            return (object)new {
                id, title = r["display_title"]!.GetValue<string>(), status = state,
                conclusion = r["conclusion"]?.GetValue<string>(), step,
                createdAt = r["created_at"]!.GetValue<string>(),
                url = $"https://github.com/{repo}/actions/runs/{id}"
            };
        }));
    }
}
