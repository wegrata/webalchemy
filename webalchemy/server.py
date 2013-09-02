import sys
import time
import logging

import os
import os.path

import tornado
import tornado.web
import tornado.ioloop
import tornado.websocket

from tornado import gen

from webalchemy.remotedocument import remotedocument


# logger for internal purposes
log= logging.getLogger(__name__)



curr_session_counter=0
def generate_session_id():
    global curr_session_counter
    curr_session_counter+=1
    return 's'+str(curr_session_counter)+'p'+str(os.getpid())




class MainHandler(tornado.web.RequestHandler):
    def initialize(self, port, host):
        log.info('Initiallizing new app!')
        ffn=os.path.realpath(__file__)
        ffn=os.path.dirname(ffn)
        ffn=os.path.join(ffn,'main.html') 
        with open(ffn,'r') as f:
            self.main_html= f.read().replace('PORT',str(port)).replace('HOST',host)
    @gen.coroutine
    def get(self):
        self.write(self.main_html)
  



class WebSocketHandler(tornado.websocket.WebSocketHandler):

    @gen.coroutine
    def initialize(self, local_doc_class, shared_wshandlers):
        log.info('Initiallizing new documet!')
        self.remotedocument= remotedocument()
        self.sharedhandlers= shared_wshandlers
        self.local_doc= local_doc_class()
        self.local_doc_initialized= False
        self.id= generate_session_id()
        self.sharedhandlers[self.id]= self


    @gen.coroutine
    def open(self):
        self.closed=False
        log.info('WebSocket opened')

    @gen.coroutine
    def on_message(self, message):
        log.info('Message received:\n'+message)
        try:
            if not self.local_doc_initialized:
                log.info('Initializing local document with message...')
                yield self.local_doc.initialize(self.remotedocument,self,message)
                self.local_doc_initialized= True
            else:
                if message.startswith('rpc: '):
                    yield self.handle_js_to_py_rpc_message(message)
                elif message.startswith('msg: '):
                    log.info('Passing message to document inmessage...')
                    yield self.local_doc.inmessage(message)
                else:
                    log.info('Discarding message...')
            yield self.flush_dom()
        except:
            log.exception('Failed handling message:')
    @gen.coroutine
    def flush_dom(self):
        code= self.remotedocument.pop_all_code()
        if code!='':
            log.info('FLUSHING DOM WITH FOLLOWING MESSAGE:\n'+code)
            # this is good to simulate latency
            #yield async_delay(2)
            self.write_message(code)
        else:
            log.info('FLUSHING DOM: **NOTHING TO FLUSH**')
    @gen.coroutine
    def msg_to_sessions(self,msg,send_to_self=False,to_session_ids=None):
        log.info('Sending message to sessions '+str(len(self.sharedhandlers))+' documents in process:')
        log.info('Message: '+msg)
        if not to_session_ids:
            lst= self.sharedhandlers.keys()
        else:
            lst= to_session_ids
        for k in lst:
            h= self.sharedhandlers[k]
            if h is not self or send_to_self:
                try:
                    yield h.local_doc.outmessage(self.id,msg)
                    yield h.flush_dom()
                except:
                    log.exception('Failed handling outmessage. Exception:')
    @gen.coroutine
    def on_close(self):
        self.closed=True
        log.info('WebSocket closed')
        log.info('Removing shared doc')
        del self.sharedhandlers[self.id]
        if hasattr(self.local_doc,'onclose'):
            log.info('Calling local document onclose:')
            try:
                yield self.local_doc.onclose()
                yield sys.stdout.flush()
            except:
                log.exception('Failed handling local document onclose. Exception:')

    @gen.coroutine
    def handle_js_to_py_rpc_message(self,msg):
        log.info('Handling message as js->py RPC call')
        pnum, *etc= msg[5:].split(',')
        pnum= int(pnum)
        args_len= etc[:pnum]
        args_txt= ''.join(etc[pnum:])
        args=[]
        curr_pos=0
        for ln in args_len:
            ln= int(ln)
            args.append(args_txt[curr_pos:curr_pos+ln])
            curr_pos+= ln
        fname, *args= args
        if fname not in js_to_py_rpcdict:
            raise Exception('Function not found in js->py RPC table: '+fname)
        log.info('Calling local function: '+fname)
        log.info('With args: '+str(args))
        try:
            yield js_to_py_rpcdict[fname](self.local_doc,self.id,*args)
        except:
            log.exception('JS RPC call failed')

    @gen.coroutine
    def rpc(self,f,*varargs,send_to_self=False,to_session_ids=None,**kwargs):
        log.info('Sending py->py rpc: '+f.__name__)
        log.info('PARAMS: varargs: '+str(varargs)+' kwargs: '+str(kwargs))
        if not to_session_ids:
            lst= self.sharedhandlers.keys()
        else:
            lst= to_session_ids
        log.info('lst='+str(lst))
        log.info('self.id='+self.id)
        for k in lst:
            h= self.sharedhandlers[k]
            if h is not self or send_to_self:
                try:
                    yield js_to_py_rpcdict[f.__name__](h.local_doc,self.id,*varargs,**kwargs)
                    yield h.flush_dom()
                except:
                    log.exception('PY RPC call failed for target session: '+k)





# decorator to register functions for js->py rpc
js_to_py_rpcdict={}
def jsrpc(f):
    log.info('registering function to js->py rpc: '+f.__name__)
    try:
        if f.__name__ in js_to_py_rpcdict:
            raise Exception('cannot decorate with js->py rpc since name already exists: '+f.__name__)
        js_to_py_rpcdict[f.__name__]=f
        return f
    except:
        log.exception('Failed registering js->py RPC function')

# decorator to register functions for py->py rpc
py_to_py_rpcdict={}
def pyrpc(f):
    log.info('registering function to py->py rpc: '+f.__name__)
    try:
        if f.__name__ in py_to_py_rpcdict:
            raise Exception('cannot decorate with py->py rpc since name already exists: '+f.__name__)
        py_to_py_rpcdict[f.__name__]=f
        return f
    except:
        log.exception('Failed registering py->py RPC function')



@gen.coroutine
def async_delay(secs):
    yield gen.Task(tornado.ioloop.IOLoop.instance().add_timeout, time.time() + secs)




def run(host,port,local_doc_class):
    shared_wshandlers= {}
    application = tornado.web.Application([
        (r'/', MainHandler, dict(host=host, port=port)),
        (r'/websocket', WebSocketHandler, dict(local_doc_class=local_doc_class, shared_wshandlers=shared_wshandlers)),
    ])
    application.listen(port)
    log.info('in run!')
    tornado.ioloop.IOLoop.instance().start()
 