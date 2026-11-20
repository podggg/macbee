#define main macbee_unused_entry
#import "../Sources/MacbeeApp/main.m"
#undef main
#include <assert.h>
int main(void){@autoreleasepool{
 MacbeeApp *app=[MacbeeApp new];assert(!app.log);
 [app append:@"before diagnostics opened\n"];
 assert([app.diagnosticBacklog isEqual:@"before diagnostics opened\n"]);
 [app append:[@"x" stringByPaddingToLength:70000 withString:@"x" startingAtIndex:0]];
 assert(app.diagnosticBacklog.length==64000);
 puts("metadata backlog retained and bounded; no windows or capture");
}return 0;}
