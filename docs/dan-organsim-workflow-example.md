real workflow of the dan organsims in my mind

use input prompt
Orchestrator recevies input prompt. 
Orchestrator prompt: 
    file reader agent:
        system prompt:
            you will read the scripts to get the project background, this is the file path XXXX
            you have access to this file read tool. 
            please read the file and get the content. and return 
        
    system prompt: 
        - you are a workflow orchestrator
        - you are responsible for orchestrating the workflow
        - please understand the project background and the context of the project, then address to the user accordingly.
        - you have access to XXX
        - we have these kind of agents, this is how to call them ... 
        - but do this gradually

    user prompt:
        - this is your last prompt, please review and see what to do next. 
    
    action:
        call file reader agent to read the file

wait for all reader to finish ... then aggregate context

Orchestrator prompt:
    you have the context now ... what to do next to address the user's prompt?

    generate prompts for workers: background, you need to do this this this tasks ... 
    or maybe generate tmp md plan files for worker: then this is the context, read the plan, implement ... 

workers, 1,2,3,4
    system prompt:
        prompt from orchestrator

        you have access to these tools... continue to implement

while true:
    for worker in workers:
        check worker status:
        if worker is done:
            orcehstator break
        else:
            orchestrator continue (but maybe launch new tasks based on current status? or stack the validation-repair look here?)

orchestrator:
    workers are done, use a validator agentxxx


while True
    validator agent:
        validate the worker's output this this this this .... do you find anywhere need to repair? 
        if so pass to repair agent, if not break 
    
    repair agent:
        repair what the validator agent found 
         pass what you repaired to the validator agent

