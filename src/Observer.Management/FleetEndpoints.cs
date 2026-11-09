using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

static class FleetEndpoints {
    public static void MapStatus(RouteGroupBuilder group, Func<HttpContext, Task<object>> status) {
        // Select the route-handler overload. The RequestDelegate overload accepts
        // Task<IResult> as Task but discards its result, producing an empty 200.
        group.MapGet("/api/status", (Delegate)(async (HttpContext context) => Results.Ok(await status(context))));
    }
}
