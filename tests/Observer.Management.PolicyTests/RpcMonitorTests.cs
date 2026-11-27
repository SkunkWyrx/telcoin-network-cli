using System.Net;
using System.Text;

static class RpcMonitorTests {
    sealed class Fake(Queue<string> answers) : HttpMessageHandler {
        public readonly List<HttpRequestMessage> Requests=[];
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken cancel) {
            Requests.Add(request);
            if(request.RequestUri!.Host.EndsWith("monitoring.azure.com")) {
                var body=await request.Content!.ReadAsStringAsync(cancel);
                if(!body.Contains("Oprail.AdiriRPC"))throw new Exception("Metric namespace changed");
                return new(HttpStatusCode.OK);
            }
            var value=answers.Dequeue();
            if(value=="fail") throw new HttpRequestException("fixture failure");
            return new(HttpStatusCode.OK) { Content=new StringContent("{\"result\":\""+value+"\"}",Encoding.UTF8,"application/json") };
        }
    }
    public static async Task Run() {
        var transport=new Fake(new(["0x7e1","0x64","0x7e1","0x69","fail","0x7e1","0x64","fail"]));
        var monitor=new RpcMonitor(new HttpClient(transport),()=>Task.FromResult("fixture-token"),"https://public.example","https://reference.example",2017,"/fixture-resource","centralus");
        var sample=await monitor.Sample(default);
        if(!sample["LagIncident"] || sample["OutageIncident"])throw new Exception("Five-block lag threshold");
        for(var i=0;i<3;i++)if(monitor.Advance(sample)["LagIncident"]!=(i==2?1:0))throw new Exception("Three-sample debounce");
        if(monitor.Advance(new(){["LagIncident"]=false})["LagIncident"]!=0)throw new Exception("Recovery reset");
        sample=await monitor.Sample(default);
        if(!sample["OutageIncident"] || sample.ContainsKey("LagIncident"))throw new Exception("Outage handling");
        sample=await monitor.Sample(default);
        if(sample["OutageIncident"] || sample.ContainsKey("LagIncident"))throw new Exception("Reference failure cannot raise lag");
        var publish=new Fake(new(["0x7e1","0x64","0x7e1","0x64"]));
        var publisher=new RpcMonitor(new HttpClient(publish),()=>Task.FromResult("fixture-token"),"https://public.example","https://reference.example",2017,"/fixture-resource","centralus");
        await publisher.Tick(default);
        var writes=publish.Requests.Where(r=>r.RequestUri!.Host=="centralus.monitoring.azure.com").ToArray();
        if(writes.Length!=2 || writes.Any(r=>r.Headers.Authorization?.Parameter!="fixture-token"))throw new Exception("Authenticated independent metrics publication");
        Console.WriteLine("RPC monitor debounce, outage/reference isolation and independent publication checks passed.");
    }
}
