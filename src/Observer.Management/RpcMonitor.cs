using System.Globalization;
using System.Net.Http.Json;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Configuration;

// Lives in the always-on management app, independently of observer/runner power.
sealed class RpcMonitor(HttpClient client, Func<Task<string>> token, string publicRpc, string referenceRpc,
                        long chainId, string resource, string region) {
    readonly Dictionary<string,int> streaks = new();
    DateTimeOffset? lastSample;
    public async Task<long> Head(string endpoint, CancellationToken cancel) {
        async Task<long> Call(string method) {
            using var response = await client.PostAsJsonAsync(endpoint, new { jsonrpc="2.0", id=1, method, @params=Array.Empty<object>() }, cancel);
            response.EnsureSuccessStatusCode();
            var data = JsonNode.Parse(await response.Content.ReadAsStringAsync(cancel))!;
            var hex = data["result"]?.GetValue<string>() ?? throw new InvalidOperationException("RPC result missing");
            if (!hex.StartsWith("0x", StringComparison.Ordinal) || hex.Length > 18) throw new InvalidOperationException("Invalid RPC quantity");
            var number=ulong.Parse(hex[2..], NumberStyles.HexNumber, CultureInfo.InvariantCulture);
            return number<=long.MaxValue ? (long)number : throw new InvalidOperationException("RPC quantity too large");
        }
        if (await Call("eth_chainId") != chainId) throw new InvalidOperationException("Wrong RPC chain");
        return await Call("eth_blockNumber");
    }
    public async Task<Dictionary<string,bool>> Sample(CancellationToken cancel) {
        long current;
        try { current=await Head(publicRpc,cancel); }
        catch when (!cancel.IsCancellationRequested) { return new() { ["OutageIncident"]=true }; }
        var signals=new Dictionary<string,bool> { ["OutageIncident"]=false };
        try { signals["LagIncident"]=await Head(referenceRpc,cancel)-current >= 5; }
        catch when (!cancel.IsCancellationRequested) { /* A reference failure is not observer lag. */ }
        return signals;
    }
    public Dictionary<string,int> Advance(Dictionary<string,bool> signals, DateTimeOffset? sampledAt=null) {
        var now=sampledAt??DateTimeOffset.UtcNow;
        if(lastSample is {} last && (now-last>TimeSpan.FromSeconds(90) || now<last))streaks.Clear();
        lastSample=now;
        foreach(var missing in streaks.Keys.Except(signals.Keys).ToArray()) streaks.Remove(missing);
        return signals.ToDictionary(p=>p.Key,p=> {
            streaks[p.Key]=p.Value ? Math.Min(streaks.GetValueOrDefault(p.Key)+1,3) : 0;
            return streaks[p.Key]>=3 ? 1 : 0;
        });
    }
    public async Task Tick(CancellationToken cancel) {
        var metrics=Advance(await Sample(cancel));
        var access=await token();
        foreach(var metric in metrics) {
            using var request=new HttpRequestMessage(HttpMethod.Post,$"https://{region}.monitoring.azure.com{resource}/metrics");
            request.Headers.Authorization=new AuthenticationHeaderValue("Bearer",access);
            // Azure's metrics ingestion rejects chunked request bodies (HTTP 411).
            request.Content=new StringContent(JsonSerializer.Serialize(new { time=DateTimeOffset.UtcNow.ToString("O"), data=new { baseData=new {
                metric=metric.Key, @namespace="Oprail.AdiriRPC", dimNames=Array.Empty<string>(),
                series=new[] { new { dimValues=Array.Empty<string>(), min=metric.Value,max=metric.Value,sum=metric.Value,count=1 } }
            } } }),Encoding.UTF8,"application/json");
            using var response=await client.SendAsync(request,cancel);response.EnsureSuccessStatusCode();
        }
    }
}

sealed class RpcMonitorWorker(IConfiguration config,IHttpClientFactory factory,ILogger<RpcMonitorWorker> log) : BackgroundService {
    protected override async Task ExecuteAsync(CancellationToken cancel) {
        if (!config.GetValue<bool>("Monitoring:Enabled")) return;
        // Fixed Adiri scope prevents this sampler from silently monitoring mainnet.
        const string scope="/subscriptions/8a149d4d-c52a-4432-929e-de754584e269/resourceGroups/oprail-observer-adiri/providers/Microsoft.Compute/virtualMachines/adiri-observer-runner";
        if (config["Fleet:Network"]!="adiri") throw new InvalidOperationException("Adiri monitoring scope required");
        var client=factory.CreateClient("monitor");
        var monitor=new RpcMonitor(client,()=>RpcApiKeys.IdentityToken(client,"https://monitoring.azure.com/"),
                                   "https://rpc-adiri.oprail.com",config["Fleet:ReferenceRpc"]!,2017,scope,"centralus");
        using var timer=new PeriodicTimer(TimeSpan.FromMinutes(1));
        while (!cancel.IsCancellationRequested) {
            try { await monitor.Tick(cancel);log.LogInformation("Independent Adiri RPC monitor sample published"); }
            catch(OperationCanceledException) when(cancel.IsCancellationRequested) { break; }
            catch(Exception error) { log.LogWarning("Adiri RPC monitor publication failed: {Type}",error.GetType().Name); }
            try { if(!await timer.WaitForNextTickAsync(cancel))break; } catch(OperationCanceledException) { break; }
        }
    }
}
