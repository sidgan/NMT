#!/usr/bin/env python

import numpy as np
from matwizard import matwizard
import theano
import theano.tensor as T
import cPickle as pkl
import multiprocessing as mp
import sys
import time
sys.setrecursionlimit(50000)
from theano.tensor.shared_randomstreams import RandomStreams
srng = RandomStreams()
#TODO Add a copies of train and batch_cost that don't use multiprocessing, but don't delete the ones that do
#TODO build word2vec and/or GloVe capabilities into Library
#TODO try with mnist and cifar10 cifar100 if you want to make life hard
#TODO read pascal vincent in iclr 2015
#TODO prepare for attention models by adding a parameter to Decoder
#TODO change how the cost is saved

#***********************************************************************
# Helper functions
#=======================================================================
# Squared difference
def squared_difference(output, target):
  """"""

  return T.sqr(output-target)/np.float32(2)

#=======================================================================
# Absolute difference
def absolute_difference(output, target):
  """"""

  return T.abs_(output-target)

#=======================================================================
# Soft step function / Logistic sigmoid
def softstep(x):
  """"""
  
  return T.nnet.sigmoid(np.float32(2)*x)

#=======================================================================
# Sharp step function 
def sharpstep(x):
  """"""
  
  return T.switch(x > 1, np.float32(1), T.switch(x < -1, np.float32(0), (x+np.float32(1))/np.float32(2)))

#=======================================================================
# Soft sign function / Hyperbolic tangent
def softsign(x):
  """"""
  
  return T.tanh(x)

#=======================================================================
# Sharp sign function 
def sharpsign(x):
  """"""
  
  return T.switch(x > 1, np.float32(1), T.switch(x < -1, np.float32(-1), x))

#=======================================================================
# Soft absolute value
def softabs(x):
  """"""
  
  return T.log(np.float32(2)*T.cosh(x)) - T.log(np.float32(2))

#=======================================================================
# Sharp absolute value
def sharpabs(x):
  """"""
  
  return T.abs_(x)

#=======================================================================
# Soft rectifier function / Softplus function
def softpo(x):
  """"""
  
  return T.nnet.softplus(np.float32(2)*x)/np.float32(2)
  
#=======================================================================
# Sharp rectifier function / ReLU
def sharppo(x):
  """"""
  
  return T.switch(x > 0, x, np.float32(0))

#=======================================================================
# Soft truth function / bathtub function
def softbool(x):
  """"""
  
  return T.sqr(T.tanh(x))

#=======================================================================
# Sharp truth function
def sharpbool(x):
  """"""
  
  return T.switch(x > 1, np.float32(1), T.switch(x < -1, np.float32(-1), T.abs_(x)))

#=======================================================================
# Soft positive function
def softpos(x):
  """"""
  
  return T.sqr(T.nnet.sigmoid(np.float32(2)*x))

#=======================================================================
# Sharp positive function
def sharppos(x):
  """"""
  
  return T.switch(x > 1, np.float32(1), T.switch(x < 0, np.float32(0), x))

#=======================================================================
# Soft max function
def softmax(x):
  """"""
  
  #return T.nnet.softmax(np.float32(2)*T.log(x.shape[0])*x) #Fastmax
  return T.nnet.softmax(2*x) #Slowmax

#=======================================================================
# Cost worker function
def cost_worker(cost_func, dataQueue, outQueue):
  """"""

  store = np.float32(0)
  for datum in iter(dataQueue.get, 'STOP'):
    
    store += cost_func(*datum)
  if store != 0:
    outQueue.put(store)
  outQueue.put('STOP')
  return True

#=======================================================================
# Gradient worker function
def grad_worker(grad_func, dataQueue, outQueue, nmutables=0):
  """"""

  store = None
  for datum in iter(dataQueue.get, 'STOP'):
    grad_info = grad_func(*datum)
    sidxs = grad_info[:nmutables]
    cost = grad_info[nmutables]
    grads = grad_info[nmutables+1:len(grad_info)-nmutables]
    sgrads = grad_info[len(grad_info)-nmutables:]
    if store is None:
      store = {}
      store['cost'] = cost
      store['grads'] = grads
      store['sgrads'] = []
      for i, sidx, sgrad in zip(range(nmutables), sidxs, sgrads):
        store['sgrads'].append({})
        for idx, grad in zip(sidx, sgrad):
          if idx not in store['sgrads'][i]:
            store['sgrads'][i][idx] = grad
          else:
            store['sgrads'][i][idx] += grad
    else:
      store['cost'] += cost
      for i, grad in enumerate(grads):
        store['grads'][i] += grad
      for i, sidx, sgrad in zip(range(nmutables), sidxs, sgrads):
        for idx, grad in zip(sidx, sgrad):
          if idx not in store['sgrads'][i]:
            store['sgrads'][i][idx] = grad
          else:
            store['sgrads'][i][idx] += grad
  if store is not None:
    outQueue.put(store)
  outQueue.put('STOP')
  return True

#=======================================================================
# Pickling worker function
def pkl_worker(childPipe, path='.', name=''):
  """"""
  
  i = 0
  msg = 'START'
  while msg != 'STOP':
    msg = childPipe.recv()
    if msg not in ('START', 'STOP'):
      i += 1
      pkl.dump(msg[0], open(os.path.join(path, '%sstate-%02d.pkl' % (name, i)), 'w'), protocol=pkl.HIGHEST_PROTOCOL)
      pkl.dump(msg[1:], open(os.path.join(path, '%scost-%02d.pkl' % (name, i)), 'w'))
  return True

tanh = softsign
hardtanh = sharpsign
sig = softstep
sigmoid = softstep
abs = sharpabs
softplus = softpo
relu = sharppo
tub = softbool

#=======================================================================
# Shortcuts
funx = {
        'softsign': softsign,
        'sharpsign': sharpsign,
        'softstep': softstep,
        'sharpstep': sharpstep,
        'softabs': softabs,
        'sharpabs': sharpabs,
        'softpo': softpo,
        'sharppo': sharppo,
        'softbool': softbool,
        'sharpbool': sharpbool,
        'softpos': softpos,
        'sharppos': sharppos,
        
        'tanh': softsign,
        'hardtanh': sharpsign,
        'sig': softstep,
        'sigmoid': softstep,
        'abs': sharpabs,
        'softplus': softpo,
        'relu': sharppo,
        'tub': softbool,
        
        'softmax': softmax,
  }
  
#***********************************************************************
# A library
class Library():
  """"""

  #=====================================================================
  # Initialize the model
  def __init__(self, keys, mat, **kwargs):
    """"""

    #-------------------------------------------------------------------
    # Keyword Arguments
    if 'start' in kwargs:
      self.start = kwargs['start']
    else:
      self.start = '<S>'

    if 'stop' in kwargs:
      self.stop = kwargs['stop']
    else:
      self.stop = '</S>'

    if 'unk' in kwargs:
      self.unk = kwargs['unk']
    else:
      self.unk = '<UNK>'

    if 'mutable' in kwargs:
      self._mutable = kwargs['mutable']
    else:
      self._mutable = True

    #-------------------------------------------------------------------
    # Set up the access keys
    if isinstance(keys, (tuple, list, set)):
      keys = set(keys)
      keys.add(self.start)
      keys.add(self.unk)
      keys.add(self.stop)
      keys = list(keys)
      self.idxs = {}
      self.strs = {}
      for i, key in enumerate(keys):
        self.idxs[key] = np.int32(i)
        self.strs[np.int32(i)] = key
    elif isinstance(keys, dict):
      if 0 in keys:
        self.strs = keys
        self.idxs = {v:k for k, v in keys.iteritems()}
      else:
        self.idxs = keys
        self.strs = {v:k for k, v in keys.iteritems()}
        
    #-------------------------------------------------------------------
    # Set up the matrix
    if isinstance(mat, int):
      mat = np.random.normal(0, 1, size=(len(keys), mat))
      self._mutable = True
    else:
      assert len(mat) == len(keys)
      mat = (mat-np.mean(mat))/np.std(mat)
    self._wsize = mat.shape[1]
    
    #-------------------------------------------------------------------
    # Set up the Theano variables
    self.hmask = theano.shared(np.ones(self._wsize, dtype='float32'))
    self.L = theano.shared(mat.astype('float32'))
    self.gL = theano.shared(np.zeros_like(mat, dtype='float32'))
    self._gidxs = set()

    #=====================================================================
    # Convert idxs to vectors
    x = T.ivector(name='x')
    self.idxs_to_vecs = theano.function(
        inputs=[x],
        outputs=self.L[x],
        allow_input_downcast=False)

    #=====================================================================
    # Convert vectors to idxs 
    v = T.fmatrix(name='v')
    self.vecs_to_idxs = theano.function(
        inputs=[v],
        outputs=T.argmin(T.sum(squared_difference(self.L[None,:,:], v[:,None,:]), axis=2), axis=1),
        allow_input_downcast=False)

    #=====================================================================
    # Update gradients
    batchSize = T.fscalar('batchSize')
    gx = T.fmatrix('gxparams')
    gidxs = T.ivector('gidxs')
    self.update_grads = theano.function(
        inputs=[batchSize, gx, gidxs],
        outputs=[],
        updates=[(self.gL, T.inc_subtensor(self.gL[gidxs], gx/batchSize))],
        allow_input_downcast=False)

    #===================================================================
    # Reset gradients
    self.reset_grads = theano.function(
        inputs=[gidxs],
        outputs=[],
        updates=[(self.gL, T.set_subtensor(self.gL[gidxs], np.float32(0)*self.gL[gidxs]))],
        allow_input_downcast=False)

  #=====================================================================
  # Update gradients
  def update_gidxs(self, gidxs):
    """"""

    self._gidxs.update(gidxs)

  #=====================================================================
  # Update gradients
  def update_lib_grads(self, batchSize, sgrads):
    """"""

    if isinstance(sgrads, dict):
      gidxs = np.zeros(len(sgrads), dtype='int32')
      grads = np.zeros((len(sgrads), self.wsize()), dtype='float32')
      for i, pair in enumerate(sgrads.iteritems()):
        gidxs[i] = pair[0]
        grads[i] = pair[1]
    elif isinstance(sgrads, (tuple, list)):
      print sgrads
      gidxs = np.array(sgrads[0])
      grads = np.array(sgrads[1])
    self.update_grads(batchSize, grads, gidxs)
    self.update_gidxs(gidxs)

  #=====================================================================
  # Reset gradients
  def reset_lib_grads(self):
    """"""
    
    self.reset_grads(self.gidxs())
    self._gidxs = set()
  
  #=====================================================================
  # Get the gradient update indices in array form
  def gidxs(self):
    """"""

    return np.array(list(self._gidxs), dtype='int32')

  #=====================================================================
  # Get mutability 
  def mutable(self):
    """"""

    return self._mutable

  #=====================================================================
  # Get word size
  def wsize(self):
    """"""

    return self._wsize

  #=====================================================================
  # Get start string
  def start_str(self):
    """"""

    return self.start

  #=====================================================================
  # Get stop string 
  def stop_str(self):
    """"""

    return self.stop

  #=====================================================================
  # Get unk string
  def unk_str(self):
    """"""

    return self.unk

  #=====================================================================
  # Get start index
  def start_idx(self):
    """"""

    return self.strs_to_idxs(self.start_str())

  #=====================================================================
  # Get stop index 
  def stop_idx(self):
    """"""

    return self.strs_to_idxs(self.stop_str())

  #=====================================================================
  # Get unk index
  def unk_idx(self):
    """"""

    return self.strs_to_idxs[self.stop_str()]

  #=====================================================================
  # Get start vector
  def start_vec(self):
    """"""

    return self.L[self.start_idx()]

  #=====================================================================
  # Get stop vector
  def stop_vec(self):
    """"""

    return self.L[self.stop_idx()]

  #=====================================================================
  # Get unk vector
  def unk_vec(self):
    """"""

    return self.L[self.unk_idx()]

  #=====================================================================
  # Convert strs to idxs
  def strs_to_idxs(self, strs):
    """"""

    if not hasattr(strs, '__iter__'):
      strs = [strs]
    return np.array([[self.idxs[s]] for s in strs], dtype='int32')

  #=====================================================================
  # Convert idxs to strs 
  def idxs_to_strs(self, idxs):
    """"""

    if not hasattr(idxs, '__iter__'):
      idxs = [idxs]
    return [self.strs[i] for i in idxs]

  #=====================================================================
  # Convert strs to vectors 
  def strs_to_vecs(self, strs):
    """"""

    return self.idxs_to_vecs(np.array(self.strs_to_idxs(strs)))

  #=====================================================================
  # Convert vectors to strs
  def vecs_to_strs(self, vectors):
    """"""

    return self.idxs_to_strs(self.vecs_to_idxs(np.array(vectors)))

  #=====================================================================
  # Get tensor variable
  def get_subtensor(self, idxs):
    """"""

    return self.L[idxs]
  
#***********************************************************************
# An interface for optimization functions
class Opt:
  """"""

  #=====================================================================
  # Run SGD (with NAG)
  def SGD(self, eta_0=.01, T_eta=1, mu_max=.95, T_mu=1, dropout=1., anneal=0, accel=0):
    """"""

    #-------------------------------------------------------------------
    # Cast everything as float32
    eta_0  = np.float32(eta_0)
    T_eta  = np.float32(T_eta)
    mu_max = np.float32(mu_max)
    T_mu   = np.float32(T_mu)
    anneal = np.float32(anneal)
    accel  = np.float32(accel)
    
    #-------------------------------------------------------------------
    # Set up the updates & givens
    grad_norm  = T.sqrt(sum(map(lambda x: T.sqr(x).sum(), self.gparams + self.gsparams)))
    not_finite = T.or_(T.isnan(grad_norm), T.isinf(grad_norm))
    updates = []
    givens = []

    #-------------------------------------------------------------------
    # Set up a variable to keep track of the iteration
    tau = theano.shared(np.float32(0), name='tau')
    updates.extend([(tau, tau+np.float32(1))])

    #-------------------------------------------------------------------
    # Set the annealing/acceleration schedule
    eta = eta_0*T.pow(T_eta/(tau+T_eta), anneal)
    mu  = mu_max*(np.float32(1)-T.pow(T_mu/(tau+T_mu), accel))

    #-------------------------------------------------------------------
    # Regular parameters
    for x, gx in zip(self.params, self.gparams):
      vx = theano.shared(np.zeros_like(x.get_value()), name='v%s' % x.name)

      updates.append((x, T.switch(not_finite, np.float32(.1)*x, x - eta*gx)))
      updates.append((vx, T.switch(not_finite, np.float32(0), mu*vx - eta*gx)))
      givens.append((x, x + mu*vx))
      
    #-------------------------------------------------------------------
    # Sparse parameters
    gidxs = []
    for L, gL in zip(self.sparams, self.gsparams):
      vL = theano.shared(np.zeros_like(L.get_value()), name='v%s' % L.name)
      
      gidxs.append(T.ivector('gidxs'))
      x = L[gidxs[-1]]
      gx = gL[gidxs[-1]]
      vx = vL[gidxs[-1]]
      
      updates.append((L, T.inc_subtensor(x, T.switch(not_finite, np.float32(-.9)*x, -eta*gx))))
      updates.append((vL, T.set_subtensor(vx, T.switch(not_finite, np.float32(0), mu*vx - eta*gx))))
      givens.append((L, T.inc_subtensor(x, mu*vx)))

    #-------------------------------------------------------------------
    # Set up the dropout
    if dropout < 1:
      for hmask in self.hmasks:
        givens.append((hmask, srng.binomial(hmask.shape, 1, dropout, dtype='float32')))

    #-------------------------------------------------------------------
    # Compile the gradient function
    grads = theano.function(
        inputs=[self.x, self.y]+gidxs,
        outputs=gidxs+[self.cost]+T.grad(self.cost, self.params+self.xparams),
        givens=givens,
        allow_input_downcast=False)
        
    #-------------------------------------------------------------------
    # Compile the sgd function
    opt = theano.function(
        inputs=gidxs,
        outputs=[],
        givens=givens,
        updates=updates,
        allow_input_downcast=False)

    #-------------------------------------------------------------------
    # Return the compiled function
    print 'SGD function compiled'
    return grads, opt

  #=====================================================================
  # Run RMSProp (with NAG)
  def RMSProp(self, eta_0=.01, T_eta=1, rho_max=.9, T_rho=1, mu_max=.95, T_mu=1, epsilon=1e-6, dropout=1., anneal=0, expand=0, accel=0):
    """"""

    #-------------------------------------------------------------------
    # Cast everything as float32
    eta_0   = np.float32(eta_0)
    T_eta   = np.float32(T_eta)
    rho_max   = np.float32(rho_max)
    T_rho   = np.float32(T_rho)
    mu_max  = np.float32(mu_max)
    T_mu    = np.float32(T_mu)
    epsilon = np.float32(epsilon)
    anneal  = np.float32(anneal)
    accel   = np.float32(accel)
    
    #-------------------------------------------------------------------
    # Set up the updates & givens
    grad_norm  = T.sqrt(sum(map(lambda x: T.sqr(x).sum(), self.gparams + self.gsparams)))
    not_finite = T.or_(T.isnan(grad_norm), T.isinf(grad_norm))
    updates = []
    givens = []

    #-------------------------------------------------------------------
    # Set up a variable to keep track of the iteration
    tau = theano.shared(np.float32(0), name='tau')
    updates.extend([(tau, tau+np.float32(1))])

    #-------------------------------------------------------------------
    # Set the annealing/expansion schedule
    eta = eta_0*T.pow(T_eta/(tau+T_eta), anneal)
    rho = rho_max*(np.float32(1)-T.pow(T_rho/(tau+T_rho), expand))
    mu  = mu_max*(np.float32(1)-T.pow(T_mu/(tau+T_mu), accel))

    #-------------------------------------------------------------------
    # Regular parameters
    for x, gx in zip(self.params, self.gparams):
      vx = theano.shared(np.zeros_like(x.get_value()), name='v%s' % x.name)
      g2x = theano.shared(np.zeros_like(x.get_value()), name='g2%s' % x.name)

      g2x_t = rho*g2x + (np.float32(1)-rho)*T.sqr(gx)
      deltax_t = gx/T.sqrt(g2x_t+epsilon)
      
      updates.append((x, T.switch(not_finite, np.float32(.1)*x, x - eta*deltax_t)))
      updates.append((g2x, T.switch(not_finite, np.float32(0), g2x_t)))
      updates.append((vx, T.switch(not_finite, np.float32(0), mu*vx - eta*deltax_t)))
      givens.append((x, x + mu*vx))
    
    #-------------------------------------------------------------------
    # Sparse parameters
    gidxs = []
    for L, gL in zip(self.sparams, self.gsparams):
      g2L = theano.shared(np.zeros_like(L.get_value()), name='g2%s' % L.name)
      vL = theano.shared(np.zeros_like(L.get_value()), name='v%s' % L.name)
      
      gidxs.append(T.ivector('gidxs'))
      x = L[gidxs[-1]]
      gx = gL[gidxs[-1]]
      vx = vL[gidxs[-1]]
      g2x = g2L[gidxs[-1]]
      
      updates.append((L, T.inc_subtensor(x, T.switch(not_finite, np.float32(-.9)*gx, -eta*deltax_t))))
      updates.append((g2L, T.set_subtensor(g2x, T.switch(not_finite, np.float32(0), g2x_t))))
      updates.append((vL, T.set_subtensor(vx, T.switch(not_finite, np.float32(0), mu*vx - eta*deltax_t))))
      givens.append((L, T.inc_subtensor(x, mu*vx)))
      
    #-------------------------------------------------------------------
    # Set up the dropout
    if dropout < 1:
      for hmask in self.hmasks:
        givens.append((hmask, srng.binomial(hmask.shape, 1, dropout, dtype='float32')))

    #-------------------------------------------------------------------
    # Compile the gradient function
    grads = theano.function(
        inputs=[self.x, self.y]+gidxs,
        outputs=gidxs+[self.cost]+T.grad(self.cost, self.params+self.xparams),
        givens=givens,
        allow_input_downcast=False)
        
    #-------------------------------------------------------------------
    # Compile the sgd function
    opt = theano.function(
        inputs=gidxs,
        outputs=[],
        givens=givens,
        updates=updates,
        allow_input_downcast=False)

    #-------------------------------------------------------------------
    # Return the compiled function
    print 'RMSProp function compiled'
    return grads, opt

  #=====================================================================
  # Run AdaDelta
  def AdaDelta(self, eta_0=1., T_eta=1, rho_max=.9, T_rho=1, epsilon=1e-6, dropout=1., anneal=0, expand=0):
    """"""

    #-------------------------------------------------------------------
    # Cast everything as float32
    eta_0  = np.float32(eta_0)
    T_eta  = np.float32(T_eta)
    rho_max  = np.float32(rho_max)
    T_rho  = np.float32(T_rho)
    epsilon = np.float32(epsilon)
    anneal = np.float32(anneal)
    expand  = np.float32(expand)
    
    #-------------------------------------------------------------------
    # Set up the updates & givens
    grad_norm  = T.sqrt(sum(map(lambda x: T.sqr(x).sum(), self.gparams+self.gsparams)))
    not_finite = T.or_(T.isnan(grad_norm), T.isinf(grad_norm))
    updates = []
    givens = []

    #-------------------------------------------------------------------
    # Set up a variable to keep track of the iteration
    tau = theano.shared(np.float32(0.), name='tau')
    updates.extend([(tau, tau+np.float32(1))])

    #-------------------------------------------------------------------
    # Set the annealing/expansion schedule
    eta = eta_0*T.pow(T_eta/(tau+T_eta), anneal)
    rho = rho_max*(np.float32(1)-T.pow(T_rho/(tau+T_rho), expand))

    #-------------------------------------------------------------------
    # Regular parameters
    for x, gx in zip(self.params, self.gparams):
      g2x = theano.shared(np.zeros_like(x.get_value()), name='g2%s' % x.name)
      delta2x = theano.shared(np.zeros_like(x.get_value()), name='delta2%s' % x.name)

      g2x_t = rho*g2x + (np.float32(1)-rho)*T.sqr(gx)
      deltax_t = T.sqrt(delta2x + epsilon)/T.sqrt(g2x_t+epsilon)*gx
      delta2x_t = rho*delta2x + (np.float32(1)-rho)*T.sqr(eta*deltax_t)
      
      updates.append((x, T.switch(not_finite, np.float32(.1)*x, x - eta*deltax_t)))
      updates.append((delta2x, T.switch(not_finite, np.float32(0), delta2x_t)))
      updates.append((g2x, T.switch(not_finite, np.float32(0), g2x_t)))
    
    #-------------------------------------------------------------------
    # Sparse parameters
    gidxs = []
    for L, gL in zip(self.sparams, self.gsparams):
      g2L = theano.shared(np.zeros_like(L.get_value()), name='g2%s' % L.name)
      delta2L = theano.shared(np.zeros_like(L.get_value()), name='delta2%s' % L.name)
      
      gidxs.append(T.ivector('gidxs'))
      x       = L[gidxs[-1]]
      gx      = gL[gidxs[-1]]
      g2x     = g2L[gidxs[-1]]
      delta2x = delta2L[gidxs[-1]]
      
      g2x_t = rho*g2x + (np.float32(1)-rho)*T.sqr(gx)
      deltax_t = gx*T.sqrt(delta2x+epsilon)/T.sqrt(g2x+epsilon)
      delta2x_t = rho*delta2x + (np.float32(1)-rho)*T.sqr(deltax_t)
      
      updates.append((L, T.inc_subtensor(x, T.switch(not_finite, np.float32(-.9)*x, -eta*deltax_t))))
      updates.append((delta2L, T.set_subtensor(delta2x, T.switch(not_finite, np.float32(0), delta2x_t))))
      updates.append((g2L, T.set_subtensor(g2x, T.switch(not_finite, np.float32(0), g2x_t))))

    #-------------------------------------------------------------------
    # Set up the dropout
    if dropout < 1:
      for hmask in self.hmasks:
        givens.append((hmask, srng.binomial(hmask.shape, 1, dropout, dtype='float32')))

    #-------------------------------------------------------------------
    # Compile the gradient function
    grads = theano.function(
        inputs=[self.x, self.y]+gidxs,
        outputs=gidxs+[self.cost]+T.grad(self.cost, self.params+self.xparams),
        givens=givens,
        allow_input_downcast=False)
        
    #-------------------------------------------------------------------
    # Compile the sgd function
    opt = theano.function(
        inputs=gidxs,
        outputs=[],
        givens=givens,
        updates=updates,
        allow_input_downcast=False)

    #-------------------------------------------------------------------
    # Return the compiled function
    print 'AdaDelta function compiled'
    return grads, opt
  
  #=====================================================================
  # Run Adam
  def Adam(self, eta_0=.05, T_eta=1, rho1_max=.9, rho2_max=.99, T_rho=1, epsilon=1e-6, dropout=1., anneal=0, expand=0):
    """"""

    #-------------------------------------------------------------------
    # Cast everything as float32
    eta_0  = np.float32(eta_0)
    T_eta  = np.float32(T_eta)
    rho1_max = np.float32(rho1_max)
    rho2_max = np.float32(rho2_max)
    T_rho   = np.float32(T_rho)
    anneal = np.float32(anneal)
    expand  = np.float32(expand)
    
    #-------------------------------------------------------------------
    # Set up the updates & givens
    grad_norm  = T.sqrt(sum(map(lambda x: T.sqr(x).sum(), self.gparams + self.gsparams)))
    not_finite = T.or_(T.isnan(grad_norm), T.isinf(grad_norm))
    updates = []
    givens = []

    #-------------------------------------------------------------------
    # Set up a variable to keep track of the iteration
    tau = theano.shared(np.float32(0), name='tau')
    updates.extend([(tau, tau+np.float32(1))])

    #-------------------------------------------------------------------
    # Set the annealing schedule
    eta = eta_0*T.pow(T_eta/(tau+T_eta), anneal)
    rho1 = rho1_max*(np.float32(1)-T.pow(T_rho/(tau+T_rho), expand))
    rho2 = rho2_max*(np.float32(1)-T.pow(T_rho/(tau+T_rho), expand))

    #-------------------------------------------------------------------
    # Regular parameters
    for x, gx in zip(self.params, self.gparams):
      mx = theano.shared(np.zeros_like(x.get_value()), name='m%s' % x.name)
      vx = theano.shared(np.zeros_like(x.get_value()), name='v%s' % x.name)

      mx_t = (rho1*mx + (np.float32(1)-rho1)*gx) / (np.float32(1)-rho1)
      vx_t = (rho2*vx + (np.float32(1)-rho2)*T.sqr(gx)) / (np.float32(1)-rho2)
      deltax_t = mx_t / (T.sqrt(vx_t) + epsilon)
      
      updates.append((x, T.switch(not_finite, np.float32(.1)*x, x - eta*deltax_t)))
      updates.append((mx, T.switch(not_finite, np.float32(0), mx_t)))
      updates.append((vx, T.switch(not_finite, np.float32(0), vx_t)))
      
    #-------------------------------------------------------------------
    # Sparse parameters
    gidxs = []
    for L, gL in zip(self.sparams, self.gsparams):
      mL = theano.shared(np.zeros_like(L.get_value()), name='m%s' % L.name)
      vL = theano.shared(np.zeros_like(L.get_value()), name='v%s' % L.name)
      
      gidxs.append(T.ivector('gidxs'))
      x  = L[gidxs[-1]]
      gx = gL[gidxs[-1]]
      mx = mL[gidxs[-1]]
      vx = vL[gidxs[-1]]
      
      mx_t = (rho1*mx + (np.float32(1)-rho1)*gx) / (np.float32(1)-rho1)
      vx_t = (rho2*vx + (np.float32(1)-rho2)*T.sqr(gx)) / (np.float32(1)-rho2)
      deltax_t = mx_t / (T.sqrt(vx_t) + epsilon)
      
      updates.append((L, T.inc_subtensor(x, T.switch(not_finite, np.float32(-.9)*x, -eta*deltax_t))))
      updates.append((mL, T.set_subtensor(mx, T.switch(not_finite, np.float32(0), mx_t))))
      updates.append((vL, T.set_subtensor(vx, T.switch(not_finite, np.float32(0), vx_t))))

    #-------------------------------------------------------------------
    # Set up the dropout
    if dropout < 1:
      for hmask in self.hmasks:
        givens.append((hmask, srng.binomial(hmask.shape, 1, dropout, dtype='float32')))

    #-------------------------------------------------------------------
    # Compile the gradient function
    grads = theano.function(
        inputs=[self.x, self.y]+gidxs,
        outputs=gidxs+[self.cost]+T.grad(self.cost, self.params+self.xparams),
        givens=givens,
        allow_input_downcast=False)
        
    #-------------------------------------------------------------------
    # Compile the sgd function
    opt = theano.function(
        inputs=gidxs,
        outputs=[],
        givens=givens,
        updates=updates,
        allow_input_downcast=False)

    #-------------------------------------------------------------------
    # Return the compiled function
    print 'Adam function compiled'
    return grads, opt

#***********************************************************************
# A multilayer neural classifier
class Classifier(Opt):
  """"""
  
  #=====================================================================
  # Initialize the network
  def __init__(self, libs, dims, **kwargs):
    """"""
    
    #-------------------------------------------------------------------
    # Keyword arguments
    if 'sharelib' in kwargs:
      self.sharelib = np.float32(kwargs['sharelib'])
    else:
      self.sharelib = False
    
    if 'hfunc' in kwargs:
      self.hfunc = kwargs['hfunc']
    else:
      self.hfunc = 'tanh'

    if 'L1reg' in kwargs:
      self.L1reg = np.float32(kwargs['L1reg'])
    else:
      self.L1reg = np.float32(0)

    if 'L2reg' in kwargs:
      self.L2reg = np.float32(kwargs['L2reg'])
    else:
      self.L2reg = np.float32(0)

    self.sparams = []
    self.gsparams = []
    
    self.Wparams  = []
    self.Wbparams = []
    self.Lparams  = []
    self.Lbparams = []
    self.hmasks   = []
    
    #-------------------------------------------------------------------
    # Initialize the model params
    for i in xrange(1, len(dims)):
      self.Wparams.append(theano.shared(matwizard(dims[i], dims[i-1], output=self.hfunc, imput=(self.hfunc if i > 1 else '')).T, name='W-%d' % i))
      self.Wbparams.append(theano.shared(np.zeros(dims[i], dtype='float32'), name='b-%d' % i))
      self.hmasks.append(theano.shared(np.ones(dims[i], dtype='float32'), name='hmask-%d' % i))
      
    #-------------------------------------------------------------------
    # Initialize the classifier params
    self.libs = []
    ldims = []
    for l, lib in enumerate(libs):
      if not isinstance(lib, Library):
        lib = Library(*lib)
      self.libs.append(lib)
      ldims.append(lib.wsize())
      if self.sharelib:
        assert lib.wsize() == dims[-1]
        lib.L.name = 'W%d-L%d' % (i+1, l)
        self.Lparams.append(lib.L)
      else:
        self.Lparams.append(theano.shared(matwizard(len(lib.idxs), dims[-1], output='softmax', spar=15).T, name='W%d-L%d' % (i+1, l)))
      self.Lbparams.append(theano.shared(np.zeros(len(lib.idxs), dtype='float32'), name='b%d-L%d' % (i+1, l)))
    #-------------------------------------------------------------------
    # Build the input/output variables
    self.x = T.fmatrix('x')
    self.y = T.imatrix('y')
    
    #-------------------------------------------------------------------
    # Bundle the params
    self.params =\
        self.Wparams +\
        self.Lparams +\
        self.Wbparams +\
        self.Lbparams
    self.gparams = [theano.shared(np.zeros_like(param.get_value()), name='g'+param.name) for param in self.params]
    
    self.h = [self.x]
    for Wparam, Wbparam, hmask in zip(self.Wparams, self.Wbparams, self.hmasks):
      a = T.dot(self.h[-1], Wparam)
      a += Wbparam
      h = funx[self.hfunc](a)*hmask
      self.h.append(h)
    self.o = []
    for Lparam, Lbparam in zip(self.Lparams, self.Lbparams):
      a = T.dot(self.h[-1], (Lparam.T if self.sharelib else Lparam))
      a += Lbparam
      o = softmax(a)
      self.o.append(o)
    self.m = [T.argmax(o, axis=1) for o in self.o]
    
    #-------------------------------------------------------------------
    # Build the cost variable
    self.error = np.float32(0)
    for i in xrange(len(self.o)):
      self.error += T.nnet.categorical_crossentropy(self.o[i], self.y[:,i])
    self.error = T.mean(self.error)
    
    self.complexity = theano.shared(np.float32(0))
    if self.L1reg > 0:
      self.complexity += self.L1reg*T.sum([T.sum(T.abs_(Wparam)) for Wparam in self.Wparams])
      self.complexity += self.L1reg*T.sum([T.sum(T.abs_(Lparam)) for Lparam in self.Lparams])
    if self.L2reg > 0:
      self.complexity += self.L2reg*T.sum([T.sum(T.sqr(Wparam)) for Wparam in self.Wparams])
      self.complexity += self.L2reg*T.sum([T.sum(T.sqr(Lparam)) for Wparam in self.Lparams])
    
    self.cost = self.error + self.complexity
    
    #===================================================================
    # Activate
    self.vecs_to_vecs = theano.function(
      inputs=[self.x],
      outputs=self.o,
      allow_input_downcast=False)
    
    #===================================================================
    # Predict
    self.vecs_to_idxs = theano.function(
      inputs=[self.x],
      outputs=self.m,
      allow_input_downcast=False)
    
    #===================================================================
    # Error
    self.vecs_to_error = theano.function(
      inputs=[self.x, self.y],
      outputs=self.error,
      allow_input_downcast=False)
    
    #===================================================================
    # Complexity
    self.vecs_to_comp = theano.function(
      inputs=[self.x],
      outputs=self.complexity,
      on_unused_input='ignore',
      allow_input_downcast=False)
    
    #===================================================================
    # Cost
    self.vecs_to_cost = theano.function(
      inputs=[self.x, self.y],
      outputs=self.cost,
      allow_input_downcast=False)
    
    #===================================================================
    # Update gradients
    batchSize = T.scalar('batchSize')
    paramVars =\
      [T.fmatrix() for Wparam in self.Wparams] +\
      [T.fmatrix() for Lparam in self.Lparams] +\
      [T.fvector() for Wbparam in self.Wbparams] +\
      [T.fvector() for Lbparam in self.Lbparams]
    self.update_grads = theano.function(
      inputs=[batchSize]+paramVars,
      outputs=[],
      updates=[(gparam, gparam+paramVar/batchSize) for gparam, paramVar in zip(self.gparams, paramVars)],
      allow_input_downcast=False)
    
    #===================================================================
    # Reset gradients
    self.reset_grad = theano.function(
      inputs=[],
      outputs=[],
      updates=[(gparam, np.float32(0)*gparam) for gparam in self.gparams],
      allow_input_downcast=False)
  
  #=====================================================================
  # Convert the dataset to the expected format
  def convert_dataset(self, dataset):
    """"""
    
    return (np.array([datum[0] for datum in dataset]).astype('float32'), np.array([datum[1] for datum in dataset]).astype('int32'))
  
  #=====================================================================
  # Calculate the cost of a minibatch using multiple processes
  def mp_batch_cost(self, dataset, workers=2):
    """"""
    
    cost = 0
    dataQueue = mp.Queue()
    costQueue = mp.Queue()
    processes = []
    miniSize = int(len(dataset)/workers)
    i = -1
    for i in xrange(workers-1):
      dataQueue.put((dataset[0][i*miniSize:(i+1)*miniSize], dataset[1][i*miniSize:(i+1)*miniSize]))
    dataQueue.put((dataset[0][(i+1)*miniSize:], dataset[1][(i+1)*miniSize:]))
    for worker in xrange(workers):
      dataQueue.put('STOP')
    for worker in xrange(workers):
      process = mp.Process(target=cost_worker, args=(self.vecs_to_cost, dataQueue, costQueue))
      process.start()
      processes.append(process)
    for worker in xrange(workers):
      for miniCost in iter(costQueue.get, 'STOP'):
        cost += miniCost
    for process in processes:
      process.join()
    return cost / len(dataset)
  
  #=====================================================================
  # Calculate the cost of the whole dataset
  def batch_cost(self, dataset):
    """"""
    
    return self.vecs_to_cost(*dataset)
    
  #=====================================================================
  # Calculate the gradients of a minibatch using multiple cores
  def train(self, dataset, grader, optimizer, batchSize=64, epochs=1, costEvery=None, testset=None, saveEvery=None, savePipe=None):
    """"""
    
    #-------------------------------------------------------------------
    # Saving and printing
    s = ''
    epochd = str(int(np.log10(epochs))+1)
    minibatchd = str(int(np.log10(len(dataset[0])/batchSize))+1)
    s += ('Minibatch %0'+epochd+'d-%0'+minibatchd+'d') %(0,0)
    cost = []
    cost.append(self.batch_cost(dataset))
    s += ': %.3f train error' % cost[-1]
    if testset is not None:
      test = []
      test.append(self.batch_cost(testset))
      s += ', %.3f test error' % test[-1]
    wps = 0.0
    s += ', %.1f data per second' % wps
    if saveEvery is not None:
      savePipe.send((self, cost) + ((test,) if testset is not None else tuple()))
      lastSaveTime = time.time()
      s += ', %.1f minutes since saving' % ((time.time()-lastSaveTime)/60)
    s += '        \r'
    print s,
    sys.stdout.flush()
    lastCostTime = time.time()
    
    #-------------------------------------------------------------------
    # Multiprocessing the minibatch
    recentCost = []
    for t in xrange(epochs):
      dataidxs = np.arange(len(dataset[0]))
      np.random.shuffle(dataidxs)
      for mb in xrange(len(dataset[0])/batchSize):
        grad_info = grader(dataset[0][dataidxs[mb*batchSize:(mb+1)*batchSize]], dataset[1][dataidxs[mb*batchSize:(mb+1)*batchSize]])
        recentCost.append(grad_info[0])
        self.update_grads(batchSize, *grad_info[1:])
        optimizer()
        self.reset_grad()
    
        #---------------------------------------------------------------
        # More printing and saving
        if costEvery is not None and (mb+1) % costEvery == 0:
          cost.append(np.mean(recentCost))
          recentCost = []
          if testset is not None:
            test.append(self.batch_cost(testset))
          thisCostTime = time.time()
        if saveEvery is not None and (mb+1) % saveEvery == 0:
          savePipe.send((self, cost) + ((test,) if testset is not None else tuple()))
          lastSaveTime = time.time()
        if costEvery is not None and (mb+1) % costEvery == 0:
          s = ''
          s += ('Minibatch %0'+epochd+'d-%0'+minibatchd+'d') % (t+1, mb+1)
          s += ': %.3f train error' % cost[-1]
          if testset is not None:
            s += ': %.3f test error' % test[-1]
          if wps == 0:
            wps = ((batchSize * costEvery) / (thisCostTime-lastCostTime))
          else:
            wps = .67*wps + .33*((batchSize*costEvery) / (thisCostTime-lastCostTime))
          s += ', %.1f data per second' % wps
          if saveEvery is not None:
            s += ', %.1f minutes since saving' % (time.time()-lastSaveTime)
          s += '        \r'
          print s,
          sys.stdout.flush()
          lastCostTime = time.time()

      #-----------------------------------------------------------------
      # If we haven't been printing, print now
      if not (costEvery is not None and (mb+1) % costEvery == 0):
        cost.append(np.mean(recentCost))
        recentCost = []
        if testset is not None:
          test.append(self.batch_cost(testset))
        thisCostTime = time.time()
      if saveEvery is not None and (mb+1) % saveEvery != 0:
        savePipe.send((self, cost) + ((test,) if testset is not None else tuple()))
        lastSaveTime = time.time()
      s = ''
      s += ('Minibatch %0'+epochd+'d-%0'+minibatchd+'d') % (t+1,mb+1)
      s += ': %.3f train error' % cost[-1]
      if testset is not None:
        s += ': %.3f test error' % test[-1]
      if wps == 0:
        wps = .67*wps + .33*((batchSize*((mb+1) % costEvery) if costEvery is not None else len(dataset[0])) / (thisCostTime-lastCostTime))
      s += ', %.1f data per second' % wps
      if saveEvery is not None:
        s += ', %.1f minutes since saving' % ((time.time() - lastSaveTime)/60)
      s += '        \r'
      print s
      sys.stdout.flush()
      if costEvery is None or (mb+1) % costEvery != 0:
        lastCostTime = time.time()
    
    #-------------------------------------------------------------------
    # Wrap everything up
    if savePipe is not None:
      savePipe.send((self, cost) + ((test,) if testset is not None else tuple()))
      savePipe.send('STOP')
    print ''
    return cost
  
  #=====================================================================
  # Calculate the gradients of a minibatch using multiple cores
  def mp_train(self, dataset, grader, optimizer, batchSize=64, epochs=1, costEvery=None, testset=None, saveEvery=None, savePipe=None, workers=2):
    """"""
    
    #-------------------------------------------------------------------
    # Saving and printing
    s = ''
    epochd = str(int(np.log10(epochs))+1)
    minibatchd = str(int(np.log10(len(dataset[0])/batchSize))+1)
    s += ('Minibatch %0'+epochd+'d-%0'+minibatchd+'d') %(0,0)
    cost = []
    cost.append(self.mp_batch_cost(dataset, workers))
    s += ': %.3f train error' % cost[-1]
    if testset is not None:
      test = []
      test.append(self.mp_batch_cost(testset, workers))
      s += ', %.3f test error' % test[-1]
    wps = 0.0
    s += ', %.1f data per second' % wps
    if saveEvery is not None:
      savePipe.send((self, cost) + ((test,) if testset is not None else tuple()))
      lastSaveTime = time.time()
      s += ', %.1f minutes since saving' % ((time.time()-lastSaveTime)/60)
    s += '        \r'
    print s,
    sys.stdout.flush()
    lastCostTime = time.time()
    
    #-------------------------------------------------------------------
    # Multiprocessing the minibatch
    dataQueue = mp.Queue()
    gradQueue = mp.Queue()
    recentCost = []
    for t in xrange(epochs):
      dataidxs = np.arange(len(dataset[0]))
      np.random.shuffle(dataidxs)
      for mb in xrange(len(dataset[0])/batchSize):
        processes = []
        miniSize = int(batchSize/workers)
        i = -1
        for i in xrange(workers-1):
          dataQueue.put((dataset[0][dataidxs[i*miniSize:(i+1)*miniSize]], dataset[1][dataidxs[i*miniSize:(i+1)*miniSize]]))
        dataQueue.put((dataset[0][dataidxs[(i+1)*miniSize:]], dataset[1][dataidxs[(i+1)*miniSize:]]))
        for worker in xrange(workers):
          dataQueue.put('STOP')
        for worker in xrange(workers):
          process = mp.Process(target=grad_worker, args=(grader, dataQueue, gradQueue))
          process.start()
          processes.append(process)
        for worker in xrange(workers):
          for grads in iter(gradQueue.get, 'STOP'):
            recentCost.append(grads['cost'])
            self.update_grads(batchSize, *grads['grads'])
        for process in processes:
          process.join()
        optimizer()
        self.reset_grad()
        sys.exit()
    
        #---------------------------------------------------------------
        # More printing and saving
        if costEvery is not None and (mb+1) % costEvery == 0:
          cost.append(np.sum(recentCost)/(batchSize*costEvery))
          recentCost = []
          if testset is not None:
            test.append(self.mp_batch_cost(testset, workers))
          thisCostTime = time.time()
        if saveEvery is not None and (mb+1) % saveEvery == 0:
          savePipe.send((self, cost) + ((test,) if testset is not None else tuple()))
          lastSaveTime = time.time()
        if costEvery is not None and (mb+1) % costEvery == 0:
          s = ''
          s += ('Minibatch %0'+epochd+'d-%0'+minibatchd+'d') % (t+1, mb+1)
          s += ': %.3f train error' % cost[-1]
          if testset is not None:
            s += ': %.3f test error' % test[-1]
          if wps == 0:
            wps = ((batchSize * costEvery) / (thisCostTime-lastCostTime))
          else:
            wps = .67*wps + .33*((batchSize*costEvery) / (thisCostTime-lastCostTime))
          s += ', %.1f data per second' % wps
          if saveEvery is not None:
            s += ', %.1f minutes since saving' % (time.time()-lastSaveTime)
          s += '        \r'
          print s,
          sys.stdout.flush()
          lastCostTime = time.time()

      #-----------------------------------------------------------------
      # If we haven't been printing, print now
      if costEvery is None or (mb+1) % costEvery != 0:
        if costEvery is None:
          cost.append(np.sum(recentCost)/len(dataset[0]))
        else:
          cost.append(np.sum(recentCost)/(len(dataset[0]) % (batchSize*costEvery)))
        recentCost = []
        if testset is not None:
          test.append(self.batch_cost(testset, workers))
        thisCostTime = time.time()
      if saveEvery is not None and (mb+1) % saveEvery != 0:
        savePipe.send((self, cost) + ((test,) if testset is not None else tuple()))
        lastSaveTime = time.time()
      s = ''
      s += ('Minibatch %0'+epochd+'d-%0'+minibatchd+'d') % (t+1,mb+1)
      s += ': %.3f train error' % cost[-1]
      if testset is not None:
        s += '" %.3f test error' % test[-1]
      if wps == 0:
        wps = .67*wps + .33*((batchSize*((mb+1) % costEvery) if costEvery is not None else len(dataset[0])) / (thisCostTime-lastCostTime))
      s += ', %.1f data per second' % wps
      if saveEvery is not None:
        s += ', %.1f minutes since saving' % ((time.time() - lastSaveTime)/60)
      s += '        \r'
      print s, sys.stdout.flush()
      if costEvery is None or (mb+1) % costEvery != 0:
        lastCostTime = time.time()
    
    #-------------------------------------------------------------------
    # Wrap everything up
    if savePipe is not None:
      savePipe.send((self, cost) + ((test,) if testset is not None else tuple()))
      savePipe.send('STOP')
    print ''
    return cost
  
#***********************************************************************
# A multilayer recurrent neural encoder
class Encoder(Opt):
  """"""

  #=====================================================================
  # Initialize the network
  def __init__(self, libs, dims, **kwargs):
    """"""

    #-------------------------------------------------------------------
    # Keyword arguments
    if 'model' in kwargs:
      self.model = kwargs['model']
    else:
      self.model = 'RNN'

    if 'hfunc' in kwargs:
      self.hfunc = kwargs['hfunc']
    else:
      self.hfunc = 'tanh'

    if 'window' in kwargs:
      self.window = np.float32(kwargs['window'])
    else:
      self.window = np.int32(1)

    if 'reverse' in kwargs:
      self.reverse = kwargs['reverse']
    else:
      self.reverse = False

    if 'L1reg' in kwargs:
      self.L1reg = np.float32(kwargs['L1reg'])
    else:
      self.L1reg = np.float32(0)

    if 'L2reg' in kwargs:
      self.L2reg = np.float32(kwargs['L2reg'])
    else:
      self.L2reg = np.float32(0)

    #-------------------------------------------------------------------
    # Process the libraries
    self.libs = []
    self.sparams = []
    self.gsparams = []
    ldims = []
    for i, lib in enumerate(libs):
      if not isinstance(lib, Library):
        lib = Library(*lib)
      self.libs.append(lib)
      if lib.mutable():
        self.sparams.append(lib.L)
        self.gsparams.append(lib.gL)
        lib.L.name = 'L-%d' % (i+1)
        lib.gL.name = 'gL-%d' % (i+1)
      ldims.append(lib.wsize())
    ldims = np.sum(ldims)*self.window
    assert dims[0] == ldims

    #-------------------------------------------------------------------
    # Initialize the model params
    if self.model in ('RNN',):
      gates = 1
    elif self.model in ('GRU', 'FastGRU', 'FastLSTM'): # FastLSTM couples the input/forget gate
      gates = 3
    elif self.model in ('LSTM',):
      gates = 4

    self.Wparams = []
    self.bparams = []
    self.h_0     = []
    self.hmasks  = []
    self.c_0   = []

    for i in xrange(1, len(dims)):
      W = matwizard(dims[i], dims[i-1], shape='rect', func=self.hfunc)
      U = matwizard(dims[i], dims[i], shape='diag', func=self.hfunc)
      if gates > 1:
        W = np.concatenate([W, matwizard(dims[i]*(gates-1), dims[i-1], func='sigmoid')], axis=0)
        U = np.concatenate([U, matwizard(dims[i]*(gates-1), dims[i], func='sigmoid')], axis=0)
      self.Wparams.append(theano.shared(np.concatenate([W, U], axis=1), name='W-%d' % i))
      self.bparams.append(theano.shared(np.zeros(dims[i]*gates, dtype='float32'), name='b-%d' % i))
      self.hmasks.append(theano.shared(np.ones(dims[i], dtype='float32'), name='hmask-%d' % i))
      self.h_0.append(theano.shared(np.zeros(dims[i], dtype='float32'), name='h_0-%d' % i))
      self.c_0.append(theano.shared(np.zeros(dims[i], dtype='float32'), name='c_0-%d' % i))
    self.hmasks.extend([lib.hmask for lib in self.libs])
    
    #-----------------------------------------------------------------
    # Build the input/output variables
    self.x = T.imatrix('x')
    self.xparams = []
    for i, lib in enumerate(self.libs):
      self.xparams.append(lib.get_subtensor(self.x[:,i]))
    x = T.concatenate([xparam*lib.hmask for xparam in self.xparams], axis=1)
    self.y = T.fvector('y')

    #-------------------------------------------------------------------
    # Bundle the params
    self.params =\
        self.Wparams +\
        self.bparams +\
        self.h_0 +\
        (self.c_0 if self.model.endswith('LSTM') else [])
    self.gparams = [theano.shared(np.zeros_like(param.get_value()), name='g'+param.name) for param in self.params]

    #-----------------------------------------------------------------
    # Build the activation variable
    #- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    if self.model == 'RNN':
      def recur(i, *ch_tm1):
        """"""

        c_tm1 = ch_tm1[:len(ch_tm1)/2]
        h_tm1 = ch_tm1[len(ch_tm1)/2:]
        c_t   = []
        h_t   = [x[i:i+self.window].flatten()]
        for h_tm1_l, Wparam, bparam, hmask in zip(h_tm1, self.Wparams, self.bparams, self.hmasks):
          x_t_l = T.concatenate([h_t[-1], h_tm1_l])

          a = T.dot(Wparam, x_t_l) 
          a += bparam

          c = a
          h = funx[self.hfunc](c)*hmask

          c_t.append(c)
          h_t.append(h)

        return c_t + h_t[1:]

    #- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    elif self.model == 'GRU':
      def recur(i, *ch_tm1):
        """"""

        c_tm1 = ch_tm1[:len(ch_tm1)/2]
        h_tm1 = ch_tm1[len(ch_tm1)/2:]
        c_t   = []
        h_t   = [x[i:i+self.window].flatten()]
        for h_tm1_l, Wparam, bparam, hmask in zip(h_tm1, self.Wparams, self.bparams, self.hmasks):
          sliceLen = Wparam.shape[0]/3
          x_t_l = T.concatenate([h_t[-1], h_tm1_l])

          zr = T.dot(Wparam[sliceLen:], x_t_l) + bparam[sliceLen:]
          z  = sig(zr[:sliceLen])
          r  = sig(zr[sliceLen:])
          a  = T.dot(Wparam[:sliceLen], T.concatenate([x_t_l[:h_t[-1].shape[0]], x_t_l[h_t[-1].shape[0]:]*r]))

          c = z*funx[self.hfunc](a) + (np.float32(1)-z)*h_tm1_l
          h = c*hmask

          c_t.append(c)
          h_t.append(h)

        return c_t + h_t[1:]

    #- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    elif self.model == 'FastGRU':
      def recur(i, *ch_tm1):
        """"""

        c_tm1 = ch_tm1[:len(ch_tm1)/2]
        h_tm1 = ch_tm1[len(ch_tm1)/2:]
        c_t   = []
        h_t   = [x[i:i+self.window].flatten()]
        for h_tm1_l, Wparam, bparam, hmask in zip(h_tm1, self.Wparams, self.bparams, self.hmasks):
          sliceLen = Wparam.shape[0]/3
          x_t_l = T.concatenate([h_t[-1], h_tm1_l])

          azr = T.dot(Wparam, x_t_l) + bparam
          a   = funx[self.hfunc](azr[:sliceLen])
          z   = sig(azr[sliceLen:2*sliceLen])
          r   = sig(azr[2*sliceLen:])

          c = z*a + (np.float32(1)-z)*r*h_tm1_l
          h = c*hmask

          c_t.append(c)
          h_t.append(h)

        return c_t + h_t[1:]

    #- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    elif self.model == 'LSTM':
      def recur(i, *ch_tm1):
        """"""

        c_tm1 = ch_tm1[:len(ch_tm1)/2]
        h_tm1 = ch_tm1[len(ch_tm1)/2:]
        c_t   = []
        h_t   = [x[i:i+self.window].flatten()]
        for h_tm1_l, c_tm1_l, Wparam, bparam, hmask in zip(h_tm1, c_tm1, self.Wparams, self.bparams, self.hmasks):
          sliceLen = Wparam.shape[0]/4
          x_t_l = T.concatenate([h_t[-1], h_tm1_l])

          aifo = T.dot(Wparam, x_t_l) + bparam
          a    = funx[self.hfunc](aifo[:sliceLen])
          i    = sig(aifo[sliceLen:2*sliceLen])
          f    = sig(aifo[2*sliceLen:3*sliceLen])
          o    = sig(aifo[3*sliceLen:])

          c = i*a + f*c_tm1_l
          h = funx[self.hfunc](c*o)*hmask

          c_t.append(c)
          h_t.append(h)

        return c_t + h_t[1:]

    #- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    elif self.model == 'FastLSTM':
      def recur(i, *ch_tm1):
        """"""

        c_tm1 = ch_tm1[:len(ch_tm1)/2]
        h_tm1 = ch_tm1[len(ch_tm1)/2:]
        c_t   = []
        h_t   = [x[i:i+self.window].flatten()]
        for h_tm1_l, c_tm1_l, Wparam, bparam, hmask in zip(h_tm1, c_tm1, self.Wparams, self.bparams, self.hmasks):
          sliceLen = Wparam.shape[0]/3
          x_t_l = T.concatenate([h_t[-1], h_tm1_l])

          azo = T.dot(Wparam, x_t_l) + bparam
          a   = funx[self.hfunc](azo[:sliceLen])
          z   = sig(azo[sliceLen:2*sliceLen])
          o   = sig(azo[2*sliceLen:])

          c = z*a + (np.float32(1)-z)*c_tm1_l
          h = funx[self.hfunc](c*o)*hmask

          c_t.append(c)
          h_t.append(h)

        return c_t + h_t[1:]

    states, _ = theano.scan(
        fn=recur,
        sequences=T.arange(self.x.shape[0]-(self.window-1)),
        outputs_info = self.c_0 + self.h_0)

    self.c = states[:len(states)/2]
    self.h = states[len(states)/2:]
    
    yhat = self.c[-1][-1]

    #-------------------------------------------------------------------
    # Build the cost variable
    self.error = T.mean(squared_difference(yhat, self.y))

    self.complexity = theano.shared(np.float32(0))
    if self.L1reg > 0:
      self.complexity += self.L1reg*T.sum([T.sum(T.abs_(Wparam)) for Wparam in self.Wparams])
      self.complexity += self.L1reg*T.sum([T.sum(T.abs_(xparam)) for lib, xparam in zip(self.libs, self.xparams) if lib.mutable()])
 
    if self.L2reg > 0:
      self.complexity += self.L2reg*T.sum([T.sum(T.sqr(Wparam)) for Wparam in self.Wparams])
      self.complexity += self.L2reg*T.sum([T.sum(T.sqr(xparam)) for lib, xparam in zip(self.libs, self.xparams) if lib.mutable()])

    self.cost = self.error + self.complexity

    #===================================================================
    # Activate
    self.idxs_to_vec = theano.function(
        inputs=[self.x],
        outputs=yhat,
        allow_input_downcast=False)

    #===================================================================
    # Error
    self.idxs_to_err = theano.function(
        inputs=[self.x, self.y],
        outputs=self.error,
        allow_input_downcast=False)

    #===================================================================
    # Complexity
    self.idxs_to_comp = theano.function(
        inputs=[self.x],
        outputs=self.complexity,
        on_unused_input='ignore',
        allow_input_downcast=False)

    #===================================================================
    # Cost
    self.idxs_to_cost = theano.function(
        inputs=[self.x, self.y],
        outputs=self.cost,
        allow_input_downcast=False)

    #===================================================================
    # Update gradients
    batchSize = T.scalar('batchSize')
    paramVars =\
        [T.fmatrix() for Wparam in self.Wparams] +\
        [T.fvector() for bparam in self.bparams] +\
        [T.fvector() for h_0_l in self.h_0] +\
        ([T.fvector() for c_0_l in self.c_0] if self.model.endswith('LSTM') else [])
    self.update_grads = theano.function(
        inputs=[batchSize]+paramVars,
        outputs=[],
        updates=[(gparam, gparam+paramVar/batchSize) for gparam, paramVar in zip(self.gparams, paramVars)],
        allow_input_downcast=False)

    #===================================================================
    # Reset gradients
    self.reset_grad = theano.function(
        inputs=[],
        outputs=[],
        updates=[(gparam, np.float32(0)*gparam) for gparam in self.gparams],
        allow_input_downcast=False)

  #=====================================================================
  # Pad a list of strings or string tuples
  def pad_strs(self, strings):
    """"""

    if self.reverse:
      begins = tuple([lib.stop_str()  for lib in self.libs])
      ends   = tuple([lib.start_str() for lib in self.libs])
    else:
      begins = tuple([lib.start_str() for lib in self.libs])
      ends   = tuple([lib.stop_str()  for lib in self.libs])

    # Pad the beginning
    nbegins = 0
    while tuple(strings[nbegins]) == begins:
      nbegins += 1
    strings = [begins]*(self.window-nbegins) + strings

    # Pad the end
    if tuple(strings[0]) != ends:
      strings.insert(0, ends)

    return strings
  
  #=====================================================================
  # Converts a list of strings or string tuples into a matrix
  def strs_to_idxs(self, strings):
    """"""

    if not isinstance(strings[0], (tuple, list)):
      return self.libs[0].strs_to_idxs(strings)
    else:
      return np.concatenate([lib.strs_to_idxs([string[i] for string in strings]) for i, lib in enumerate(self.libs)], axis=1)

  #=====================================================================
  # Unpad a list of strings or string tuples
  def unpad_strs(self, strings):
    """"""

    if self.reverse:
      begins = tuple([lib.stop_str()  for lib in self.libs])
      ends   = tuple([lib.start_str() for lib in self.libs])
    else:
      begins = tuple([lib.start_str() for lib in self.libs])
      ends   = tuple([lib.stop_str()  for lib in self.libs])

    # Unpad the beginning
    nbegins = 0
    while tuple(strings[nbegins]) == begins:
      nbegins += 1
    strings = strings[nbegins:]

    # Unpad the end
    if tuple(strings[-1]) == ends:
      strings.pop()

    return strings

  #=====================================================================
  # Unpad a vector of indices 
  def unpad_idxs(self, indices):
    """"""

    if self.reverse:
      begins = np.concatenate([lib.stop_idx()  for lib in self.libs])
      ends   = np.concatenate([lib.start_idx() for lib in self.libs])
    else:
      begins = np.concatenate([lib.start_idx() for lib in self.libs])
      ends   = np.concatenate([lib.stop_idx()  for lib in self.libs])

    # Unpad the beginning
    nbegins = 0
    while tuple(indices[nbegins]) == begins:
      nbegins += 1
    indices = indices[nbegins:]

    # Unpad the end
    if tuple(indices[-1]) == ends:
      indices = indices[:-1]

    return indices

  #=====================================================================
  # Unpad a vector of vectors
  def unpad_vecs(self, vectors):
    """"""

    if self.reverse:
      begins = np.concatenate([lib.stop_vec()  for lib in self.libs])
      ends   = np.concatenate([lib.start_vec() for lib in self.libs])
    else:
      begins = np.concatenate([lib.start_vec() for lib in self.libs])
      ends   = np.concatenate([lib.stop_vec()  for lib in self.libs])

    # Unpad the beginning
    nbegins = 0
    while tuple(vectors[nbegins]) == begins:
      nbegins += 1
    vectors = vectors[nbegins:]

    # Unpad the end
    if tuple(vectors[-1]) == ends:
      vectors = vectors[:-1]

    return vectors

  #=====================================================================
  # Converts a dataset into the expected format (in place)
  def convert_dataset(self, dataset):
    """"""

    for i, datum in enumerate(dataset):
      if self.reverse:
        string = datum[0][::-1]
      else:
        string = datum[0][:]
      dataset[i] = (self.strs_to_idxs(self.pad_strs(string)), datum[1])

  #=====================================================================
  # Converts a list of input strings or string tuples into a vector 
  def strs_to_vec(self, strings):
    """"""

    if self.reverse:
      strings = strings[::-1]
    else:
      strings = strings[:]

    return self.idxs_to_vec(self.strs_to_idxs(self.pad_strs(strings)))

  #=====================================================================
  # Converts a list of input strings or string tuples and an output vector into an error
  def strs_to_err(self, strings, vector):
    """"""

    if self.reverse:
      strings = strings[::-1]
    else:
      strings = strings[:]

    return self.idxs_to_err(self.strs_to_idxs(self.pad_strs(strings)), vector)

  #=====================================================================
  # Converts a list of input strings or string tuples and an output vector into a cost
  def strs_to_cost(self, strings, vector):
    """"""

    if self.reverse:
      strings = strings[::-1]
    else:
      strings = strings[:]

    return self.idxs_to_cost(self.strs_to_idxs(self.pad_strs(strings)), vector)

  #=====================================================================
  # Calculate the cost of a minibatch using multiple cores
  def mp_batch_cost(self, dataset, workers=2):
    """"""

    cost = 0
    dataQueue = mp.Queue()
    costQueue = mp.Queue()
    processes = []
    for datum in dataset:
      dataQueue.put(datum)
    for worker in xrange(workers):
      dataQueue.put('STOP')
    for worker in xrange(workers):
      process = mp.Process(target=cost_worker, args=(self.idxs_to_cost, dataQueue, costQueue))
      process.start()
      processes.append(process)
    for worker in xrange(workers):
      for miniCost in iter(costQueue.get, 'STOP'):
        cost += miniCost
    for process in processes:
      process.join()
    return cost / len(dataset)

  #=====================================================================
  # Calculate the cost of a minibatch
  def batch_cost(self, dataset):
    """"""

    cost = 0
    for datum in dataset:
      cost += self.idxs_to_cost(*datum)
    return cost / len(dataset)

  #=====================================================================
  # Calculate the gradients of a minibatch using multiple cores
  def mp_train(self, dataset, grader, optimizer, batchSize=64, epochs=1, costEvery=None, testset=None, saveEvery=None, savePipe=None, workers=2):
    """"""

    #-------------------------------------------------------------------
    # Count the number of mutable libraries
    nmutables = sum([lib.mutable() for lib in self.libs])
    
    #-------------------------------------------------------------------
    # Saving and printing
    s = ''
    epochd = str(int(np.log10(epochs))+1)
    minibatchd = str(int(np.log10(len(dataset)/batchSize))+1)
    s += ('Minibatch %0'+epochd+'d-%0'+minibatchd+'d') % (0,0)
    cost = []
    cost.append(self.mp_batch_cost(dataset, workers))
    s += ': %.3f train error' % cost[-1]
    if testset is not None:
      test = []
      test.append(self.mp_batch_cost(testset, workers))
      s += ', %.3f test error' % test[-1]
    wps = 0.0
    s += ', %.1f data per second' % wps
    if saveEvery is not None:
      savePipe.send((self, cost) + ((test,) if testset is not None else tuple()))
      lastSaveTime = time.time()
      s += ', %.1f minutes since saving' % ((time.time()-lastSaveTime)/60)
    s += '        \r'
    print s,
    sys.stdout.flush()
    lastCostTime = time.time()

    #-------------------------------------------------------------------
    # Multiprocessing the minibatch
    dataQueue = mp.Queue()
    gradQueue = mp.Queue()
    recentCost = []
    for t in xrange(epochs):
      np.random.shuffle(dataset)
      for mb in xrange(len(dataset)/batchSize):
        processes = []
        for datum in dataset[mb*batchSize:(mb+1)*batchSize]:
          sidxs = []
          for i, lib in enumerate(self.libs):
            if lib.mutable():
              sidxs.append(datum[0][:,i])
          dataQueue.put(datum+tuple(sidxs))
        for worker in xrange(workers):
          dataQueue.put('STOP')
        for worker in xrange(workers):
          process = mp.Process(target=grad_worker, args=(grader, dataQueue, gradQueue, nmutables))
          process.start()
          processes.append(process)
        for worker in xrange(workers):
          for grads in iter(gradQueue.get, 'STOP'):
            recentCost.append(grads['cost'])
            self.update_grads(batchSize, *grads['grads'])
            if nmutables > 0:
              i = 0
              for lib in self.libs:
                if lib.mutable():
                  lib.update_lib_grads(batchSize, grads['sgrads'][i])
                  i += 1
        for process in processes:
          process.join()
        optimizer(*[lib.gidxs() for lib in self.libs])
        self.reset_grad()
        for lib in self.libs:
          lib.reset_lib_grads()

        #---------------------------------------------------------------
        # More printing and saving
        if costEvery is not None  and (mb+1) % costEvery == 0:
          cost.append(np.sum(recentCost)/(batchSize*costEvery))
          recentCost = []
          if testset is not None:
            test.append(self.mp_batch_cost(testset, workers))
          thisCostTime = time.time()
        if saveEvery is not None and (mb+1) % saveEvery == 0:
          savePipe.send((self, cost) + ((test,) if testset is not None else tuple()))
          lastSaveTime = time.time()
        if costEvery is not None and (mb+1) % costEvery == 0:
          s = ''
          s += ('Minibatch %0'+epochd+'d-%0'+minibatchd+'d') % (t+1,mb+1)
          s += ': %.3f train error' % cost[-1]
          if testset is not None:
            s += ': %.3f test error' % (test[-1])
          if wps == 0:
            wps = ((batchSize*costEvery) / (thisCostTime-lastCostTime))
          else:
            wps = .67*wps + .33*((batchSize*costEvery) / (thisCostTime-lastCostTime))
          s += ', %.1f data per second' % wps
          if saveEvery is not None:
            s += ', %.1f minutes since saving' % ((time.time()-lastSaveTime)/60)
          s += '        \r'
          print s,
          sys.stdout.flush()
          lastCostTime = time.time()

      #-----------------------------------------------------------------
      # If we haven't been printing, print now
      if costEvery is None or (mb+1) % costEvery != 0:
        if costEvery is None:
          cost.append(np.sum(recentCost)/len(dataset))
        else:
          cost.append(np.sum(recentCost)/(len(dataset) % (batchSize*costEvery)))
        recentCost = []
        if testset is not None:
          test.append(self.mp_batch_cost(testset, workers))
        thisCostTime = time.time()
      if saveEvery is not None and (mb+1) % saveEvery != 0:
        savePipe.send((self, cost) + ((test,) if testset is not None else tuple()))
        lastSaveTime = time.time()
      s = ''
      s += ('Minibatch %0'+epochd+'d-%0'+minibatchd+'d') % (t+1,mb+1)
      s += ': %.3f train error' % cost[-1]
      if testset is not None:
        s += ': %.3f test error' % test[-1]
      if wps == 0:
        wps = ((batchSize*(mb+1 % costEvery) if costEvery is not None else len(dataset)) / (thisCostTime-lastCostTime))
      else:
        wps = .67*wps + .33*((batchSize*((mb+1) % costEvery) if costEvery is not None else len(dataset)) / (thisCostTime-lastCostTime))
      s += ', %.1f data per second' % wps 
      if saveEvery is not None:
        s += ', %.1f minutes since saving' % ((time.time()-lastSaveTime)/60)
      s += '        \r'
      print s,
      sys.stdout.flush()
      if costEvery is None or mb+1 % costEvery != 0:
        lastCostTime = time.time()

    #-------------------------------------------------------------------
    # Wrap everything up
    if savePipe is not None:
      savePipe.send((self, cost) + ((test,) if testset is not None else tuple()))
      savePipe.send('STOP')
    print ''
    return cost
  
  #=====================================================================
  # Calculate the gradients of a minibatch using multiple cores
  def train(self, dataset, grader, optimizer, batchSize=64, epochs=1, costEvery=None, testset=None, saveEvery=None, savePipe=None):
    """"""

    #-------------------------------------------------------------------
    # Count the number of mutable libraries
    nmutables = sum([lib.mutable() for lib in self.libs])
    
    #-------------------------------------------------------------------
    # Saving and printing
    s = ''
    epochd = str(int(np.log10(epochs))+1)
    minibatchd = str(int(np.log10(len(dataset)/batchSize))+1)
    s += ('Minibatch %0'+epochd+'d-%0'+minibatchd+'d') % (0,0)
    cost = []
    cost.append(self.batch_cost(dataset))
    s += ': %.3f train error' % cost[-1]
    if testset is not None:
      test = []
      test.append(self.batch_cost(testset))
      s += ', %.3f test error' % test[-1]
    wps = 0.0
    s += ', %.1f data per second' % wps
    if saveEvery is not None:
      savePipe.send((self, cost) + ((test,) if testset is not None else tuple()))
      lastSaveTime = time.time()
      s += ', %.1f minutes since saving' % ((time.time()-lastSaveTime)/60)
    s += '        \r'
    print s,
    sys.stdout.flush()
    lastCostTime = time.time()

    #-------------------------------------------------------------------
    # Multiprocessing the minibatch
    recentCost = []
    for t in xrange(epochs):
      np.random.shuffle(dataset)
      for mb in xrange(len(dataset)/batchSize):
        for datum in dataset[mb*batchSize:(mb+1)*batchSize]:
          sidxs = []
          for i, lib in enumerate(self.libs):
            if lib.mutable():
              sidxs.append(datum[0][:,i])
          grad_info = grader(*(datum+tuple(sidxs)))
          recentCost.append(grad_info[nmutables])
          self.update_grads(batchSize, *grad_info[nmutables+1:nmutables+1+len(self.params)])
          if nmutables > 0:
            i = 0
            for lib in self.libs:
              if lib.mutable():
                lib.update_grads(batchSize, grad_info[nmutables+1+len(self.params)+i], sidxs[i])
                i += 1
        optimizer(*[lib.gidxs() for lib in self.libs])
        self.reset_grad()
        for lib in self.libs:
          lib.reset_lib_grads()

        #---------------------------------------------------------------
        # More printing and saving
        if costEvery is not None  and (mb+1) % costEvery == 0:
          cost.append(np.sum(recentCost)/(batchSize*costEvery))
          recentCost = []
          if testset is not None:
            test.append(self.batch_cost(testset))
          thisCostTime = time.time()
        if saveEvery is not None and (mb+1) % saveEvery == 0:
          savePipe.send((self, cost) + ((test,) if testset is not None else tuple()))
          lastSaveTime = time.time()
        if costEvery is not None and (mb+1) % costEvery == 0:
          s = ''
          s += ('Minibatch %0'+epochd+'d-%0'+minibatchd+'d') % (t+1,mb+1)
          s += ': %.3f train error' % cost[-1]
          if testset is not None:
            s += ': %.3f test error' % (test[-1])
          if wps == 0:
            wps = ((batchSize*costEvery) / (thisCostTime-lastCostTime))
          else:
            wps = .67*wps + .33*((batchSize*costEvery) / (thisCostTime-lastCostTime))
          s += ', %.1f data per second' % wps
          if saveEvery is not None:
            s += ', %.1f minutes since saving' % ((time.time()-lastSaveTime)/60)
          s += '        \r'
          print s,
          sys.stdout.flush()
          lastCostTime = time.time()

      #-----------------------------------------------------------------
      # If we haven't been printing, print now
      if costEvery is None or (mb+1) % costEvery != 0:
        if costEvery is None:
          cost.append(np.sum(recentCost)/len(dataset))
        else:
          cost.append(np.sum(recentCost)/(len(dataset) % (batchSize*costEvery)))
        recentCost = []
        if testset is not None:
          test.append(self.batch_cost(testset))
        thisCostTime = time.time()
      if saveEvery is not None and (mb+1) % saveEvery != 0:
        savePipe.send((self, cost) + ((test,) if testset is not None else tuple()))
        lastSaveTime = time.time()
      s = ''
      s += ('Minibatch %0'+epochd+'d-%0'+minibatchd+'d') % (t+1,mb+1)
      s += ': %.3f train error' % cost[-1]
      if testset is not None:
        s += ': %.3f test error' % test[-1]
      if wps == 0:
        wps = ((batchSize*(mb+1 % costEvery) if costEvery is not None else len(dataset)) / (thisCostTime-lastCostTime))
      else:
        wps = .67*wps + .33*((batchSize*((mb+1) % costEvery) if costEvery is not None else len(dataset)) / (thisCostTime-lastCostTime))
      s += ', %.1f data per second' % wps 
      if saveEvery is not None:
        s += ', %.1f minutes since saving' % ((time.time()-lastSaveTime)/60)
      s += '        \r'
      print s,
      sys.stdout.flush()
      if costEvery is None or mb+1 % costEvery != 0:
        lastCostTime = time.time()

    #-------------------------------------------------------------------
    # Wrap everything up
    if savePipe is not None:
      savePipe.send((self, cost) + ((test,) if testset is not None else tuple()))
      savePipe.send('STOP')
    print ''
    return cost

#***********************************************************************
# A multilayer recurrent neural decoder 
class Decoder(Opt):
  """"""
  
  #=====================================================================
  # Initialize the network
  def __init__(self, libs, dims, **kwargs):
    """"""
    
    #-------------------------------------------------------------------
    # Keyword arguments
    if 'model' in kwargs:
      self.model = kwargs['model']
    else:
      self.model = 'RNN'

    if 'reverse' in kwargs:
      self.reverse = kwargs['reverse']
    else:
      self.reverse = False

    if 'L1reg' in kwargs:
      self.L1reg = np.float32(kwargs['L1reg'])
    else:
      self.L1reg = np.float32(0)

    if 'L2reg' in kwargs:
      self.L2reg = np.float32(kwargs['L1reg'])
    else:
      self.L2reg = np.float32(0)
    
    #-------------------------------------------------------------------
    # Process the libraries
    self.libs = []
    self.sparams = []
    self.gsparams = []
    ldims = []
    for i, lib in enumerate(libs):
      if not isinstance(lib, Library):
        lib = Library(*lib)
      self.libs.append(lib)
      ldims.append(lib.wsize())
    ldims = np.sum(ldims)
    assert dims[0] > ldims
    
    #-------------------------------------------------------------------
    # Initialize the model params
    if self.model in ('RNN',):
      gates = 1
    elif self.model in ('GRU', 'FastGRU', 'FastLSTM'): # FastLSTM couples in input/forget gate
      gates = 3
    elif self.model in ('LSTM',):
      gates = 4
    
    self.Wparams = []
    self.bparams = []
    self.hparams = []
    self.h_0     = []
    self.hmasks  = []
    self.c_0     = []
    
    for i in xrange(1, len(dims)):
      W = matwizard(dims[i], dims[i-1], shape='rect')
      U = matwizard(dims[i], dims[i], shape='diag')
      if gates > 1:
        W = np.concatenate([W, matwizard(dims[i]*(gates-1), dims[i-1], sigmoid_output=True)], axis=0)
        U = np.concatenate([U, matwizard(dims[i]*(gates-1), dims[i], sigmoid_output=True)], axis=0)
      self.Wparams.append(theano.shared(np.concatenate([W, U], axis=1), name='W-%d' % i))
      self.bparams.append(theano.shared(np.zeros(dims[i]*gates, dtype='float32'), name='b-%d' % i))
      #self.hparams.append(theano.shared(matwizard(dims[i], sigmoid_output=True), name='h-%d' % i))
      self.hparams.append(theano.shared(np.ones(dims[i], dtype='float32'), name='h-%d' % i))
      self.hmasks.append(theano.shared(np.ones(dims[i], dtype='float32'), name='hmask-%d' % i))
      self.h_0.append(theano.shared(np.zeros(dims[i], dtype='float32'), name='h_0-%d' % i))
      self.c_0.append(theano.shared(np.zeros(dims[i], dtype='float32'), name='c_0-%d' % i))
    self.hmasks.extend([lib.hmask for lib in self.libs])    
    
    #-------------------------------------------------------------------
    # Build the input/output variables
    self.x = T.fvector('x')
    self.y = T.imatrix('y')
    yparams = []
    for i, lib in enumerate(self.libs):
      yparams.append(lib.get_subtensor(self.y[:,i]))
    y = T.concatenate(yparams, axis=1)
    
    #-------------------------------------------------------------------
    # Bundle the params
    self.params =\
        self.Wparams +\
        self.bparams +\
        self.hparams +\
        self.h_0 +\
        (self.c_0 if self.model.endswith('LSTM') else [])
    self.gparams = [theano.shared(np.zeros_like(param.get_value()), name='g'+param.name) for param in self.params]
    
    #-------------------------------------------------------------------
    # Build the activation variable
    #- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    if self.model == 'RNN':
      def recur(*ch_tm1):
        """"""
        
        c_tm1 = ch_tm1[:len(ch_tm1)/2]
        h_tm1 = ch_tm1[len(ch_tm1)/2:]
        c_t   = []
        h_t   = [T.concatenate([self.x, h_tm1[-1]])]
        for h_tm1_l, Wparam, bparam, hparam, hmask in zip(h_tm1, self.Wparams, self.bparams, self.hparams, self.hmasks):
          xparam = T.concatenate([h_t[-1], h_tm1_l])
          
          a = T.dot(Wparam, xparam)
          a += bparam
          
          c = a
          h = func(c, hparam)*hmask
          
          c_t.append(c)
          h_t.append(h)
        
        return c_t + h_t[1:]
    
    #- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    elif self.model == 'GRU':
      def recur(*ch_tm1):
        """"""
        
        c_tm1 = ch_tm1[:len(ch_tm1)/2]
        h_tm1 = ch_tm1[len(ch_tm1)/2:]
        c_t   = []
        h_t   = [T.concatenate([self.x, h_tm1[-1]])]
        for h_tm1_l, Wparam, bparam, hparam, hmask in zip(h_tm1, self.Wparams, self.bparams, self.hparams, self.hmasks):
          sliceLen = Wparam.shape[0]/3
          xparam = T.concatenate([h_t[-1], h_tm1_l])

          zr = T.dot(Wparam[sliceLen:], xparam) + bparam[sliceLen:]
          z  = sig(zr[:sliceLen])
          r  = sig(zr[sliceLen:])
          a  = T.dot(Wparam[:sliceLen], T.concatenate([xparam[:h_t[-1].shape[0]], xparam[h_t[-1].shape[0]:]*r]))

          c = z*func(a, hparam) + (np.float32(1)-z)*h_tm1_l
          h = c*hmask

          c_t.append(c)
          h_t.append(h)

        return c_t + h_t[1:]
      
    #- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    elif self.model == 'FastGRU':
      def recur(*ch_tm1):
        """"""

        c_tm1 = ch_tm1[:len(ch_tm1)/2]
        h_tm1 = ch_tm1[len(ch_tm1)/2:]
        c_t   = []
        h_t   = [T.concatenate([self.x, h_tm1[-1]])]
        for h_tm1_l, Wparam, bparam, hparam, hmask in zip(h_tm1, self.Wparams, self.bparams, self.hparams, self.hmasks):
          sliceLen = Wparam.shape[0]/3
          xparam = T.concatenate([h_t[-1], h_tm1_l])

          azr = T.dot(Wparam, xparam) + bparam
          a   = func(azr[:sliceLen], hparam)
          z   = sig(zr[sliceLen:2*sliceLen])
          r   = sig(zr[2*sliceLen:])

          c = z*a + (np.float32(1)-z)*r*h_tm1_l
          h = c*hmask

          c_t.append(c)
          h_t.append(h)

        return c_t + h_t[1:]

    #- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    elif self.model == 'LSTM':
      def recur(*ch_tm1):
        """"""
        
        c_tm1 = ch_tm1[:len(ch_tm1)/2]
        h_tm1 = ch_tm1[len(ch_tm1)/2:]
        c_t   = []
        h_t   = [T.concatenate([self.x, h_tm1[-1]])]
        for h_tm1_l, c_tm1_l, Wparam, bparam, hparam, hmask in zip(h_tm1, c_tm1, self.Wparams, self.bparams, self.hparams, self.hmasks):
          sliceLen = Wparam.shape[0]/4
          xparam = T.concatenate([h_t[-1], h_tm1_l])

          aifo = T.dot(Wparam, xparam) + bparam
          a    = func(aifo[:sliceLen], hparam)
          i    = sig(aifo[sliceLen:2*sliceLen])
          f    = sig(aifo[2*sliceLen:3*sliceLen])
          o    = sig(aifo[3*sliceLen:])

          c = i*a + f*c_tm1_l
          h = func(c*o, hparam)*hmask

          c_t.append(c)
          h_t.append(h)

        return c_t + h_t[1:]
      
    #- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    elif self.model == 'FastLSTM':
      def recur(*ch_tm1):
        """"""
        
        c_tm1 = ch_tm1[:len(ch_tm1)/2]
        h_tm1 = ch_tm1[len(ch_tm1)/2:]
        c_t   = []
        h_t   = [T.concatenate([self.x, h_tm1[-1]])]
        for h_tm1_l, c_tm1_l, Wparam, bparam, hparam, hmask in zip(h_tm1, c_tm1, self.Wparams, self.bparams, self.hparams, self.hmasks):
          sliceLen = Wparam.shape[0]/3
          xparam = T.concatenate([h_t[-1], h_tm1_l])

          azo = T.dot(Wparam, xparam) + bparam
          a   = func(azo[:sliceLen], hparam)
          z   = sig(azo[sliceLen:2*sliceLen])
          o   = sig(azo[2*sliceLen:])

          c = z*a + (np.float32(1)-z)*c_tm1_l
          h = func(c*o, hparam)*hmask

          c_t.append(c)
          h_t.append(h)

        return c_t + h_t[1:]
      
    states, _ = theano.scan(
        fn=recur,
        n_steps=self.y.shape[0],
        outputs_info = self.c_0 + self.h_0)

    self.c = states[:len(states)/2]
    self.h = states[len(states)/2:]
    
    yhat = self.c[-1]
    
    #-------------------------------------------------------------------
    # Build the cost variable
    self.error = T.mean(squared_difference(yhat, y))

    self.complexity = theano.shared(np.float32(0))
    if self.L1reg > 0:
      self.complexity += self.L1reg*T.sum([T.sum(T.abs_(Wparam)) for Wparam in self.Wparams])
      self.complexity += self.L1reg*T.sum(T.abs_(self.x))
 
    if self.L2reg > 0:
      self.complexity += self.L2reg*T.sum([T.sum(T.sqr(Wparam)) for Wparam in self.Wparams])
      self.complexity += self.L2reg*T.sum(T.sqr(self.x))

    self.cost = self.error + self.complexity
    
    #===================================================================
    # Activate
    #self.vec_to_vecs = theano.function(
    #    inputs=[self.x],
    #    outputs=yhat,
    #    allow_input_downcast=False)
    
    #===================================================================
    # Error
    self.vec_to_err = theano.function(
        inputs=[self.x, self.y],
        outputs=self.error,
        allow_input_downcast=False)
    
    #===================================================================
    # Complexity
    #self.vec_to_comp = theano.function(
    #    inputs=[self.x],
    #    outputs=self.complexity,
    #    allow_input_downcast=False)
    
    #===================================================================
    # Cost
    self.vec_to_cost = theano.function(
        inputs=[self.x, self.y],
        outputs=self.cost,
        allow_input_downcast=False)
    
    #===================================================================
    # Update gradients
    batchSize = T.scalar('batchSize')
    paramVars =\
        [T.fmatrix() for Wparam in self.Wparams] +\
        [T.fvector() for bparam in self.bparams] +\
        [T.fvector() for hparam in self.hparams] +\
        [T.fvector() for h_0_l in self.h_0] +\
        ([T.fvector() for c_0_l in self.c_0] if self.model.endswith('LSTM') else [])
    self.update_grads = theano.function(
        inputs=[batchSize]+paramVars,
        outputs=[],
        updates=[(gparam, gparam+paramVar/batchSize) for gparam, paramVar in zip(self.gparams, paramVars)],
        allow_input_downcast=False)
    
    #===================================================================
    # Reset gradients
    self.reset_grad = theano.function(
        inputs=[],
        outputs=[],
        updates=[(gparam, np.float32(0)*gparam) for gparam in self.gparams],
        allow_input_downcast=False)
    
  #=====================================================================
  # Pad a list of strings or string tuples
  def pad_strs(self, strings):
    """"""

    if self.reverse:
      begins = tuple([lib.stop_str()  for lib in self.libs])
      ends   = tuple([lib.start_str() for lib in self.libs])
    else:
      begins = tuple([lib.start_str() for lib in self.libs])
      ends   = tuple([lib.stop_str()  for lib in self.libs])

    # Pad the beginning
    nbegins = 0
    while tuple(strings[nbegins]) == begins:
      nbegins += 1
    strings = [begins] + strings

    # Pad the end
    if tuple(strings[0]) != ends:
      strings.insert(0, ends)

    return strings
  
  #=====================================================================
  # Pad a matrix of indices
  def pad_idxs(self, indices):
    """"""

    if self.reverse:
      begins = np.concatenate([lib.stop_idx()  for lib in self.libs])
      ends   = np.concatenate([lib.start_idx() for lib in self.libs])
    else:
      begins = np.concatenate([lib.start_idx() for lib in self.libs])
      ends   = np.concatenate([lib.stop_idx()  for lib in self.libs])

    # Calculate the beginning padding
    nbegins = 0
    while np.equal(indices[nbegins], begins):
      nbegins += 1

    # Calculate the ending padding
    nends = 0
    if np.equal(indices[-1], ends):
      nends += 1
    a = np.empty(len(indices)+nbegins+end)
    a[0:1] = begins
    a[-1-(1-nends):-1] = ends
    
    return a

  #=====================================================================
  # Pad a matrix of vectors 
  def pad_vecs(self, vectors):
    """"""

    if self.reverse:
      begins = np.concatenate([lib.stop_vec()  for lib in self.libs])
      ends   = np.concatenate([lib.start_vec() for lib in self.libs])
    else:
      begins = np.concatenate([lib.start_vec() for lib in self.libs])
      ends   = np.concatenate([lib.stop_vec()  for lib in self.libs])

    # Calculate the beginning padding
    nbegins = 0
    while np.equal(vectors[nbegins], begins):
      nbegins += 1

    # Calculate the ending padding
    nends = 0
    if np.equal(vectors[-1], ends):
      nends += 1
    a = np.empty(len(vectors)+nbegins+end)
    a[0:1] = begins
    a[-1-(1-nends):-1] = ends
    
    return a

  #=====================================================================
  # Converts a list of strings or string tuples into a matrix
  def strs_to_idxs(self, strings):
    """"""

    if not isinstance(strings[0], (tuple, list)):
      return self.libs[0].strs_to_idxs(strings)
    else:
      return np.concatenate([lib.strs_to_idxs([string[i] for string in strings]) for i, lib in enumerate(self.libs)], axis=1)

  #=====================================================================
  # Unpad a list of strings or string tuples
  def unpad_strs(self, strings):
    """"""

    if self.reverse:
      begins = tuple([lib.stop_str()  for lib in self.libs])
      ends   = tuple([lib.start_str() for lib in self.libs])
    else:
      begins = tuple([lib.start_str() for lib in self.libs])
      ends   = tuple([lib.stop_str()  for lib in self.libs])

    # Unpad the beginning
    nbegins = 0
    while tuple(strings[nbegins]) == begins:
      nbegins += 1
    strings = strings[nbegins:]

    # Unpad the end
    if tuple(strings[-1]) == ends:
      strings.pop()

    return strings

  #=====================================================================
  # Unpad a vector of indices 
  def unpad_idxs(self, indices):
    """"""

    if self.reverse:
      begins = np.concatenate([lib.stop_idx()  for lib in self.libs])
      ends   = np.concatenate([lib.start_idx() for lib in self.libs])
    else:
      begins = np.concatenate([lib.start_idx() for lib in self.libs])
      ends   = np.concatenate([lib.stop_idx()  for lib in self.libs])

    # Unpad the beginning
    nbegins = 0
    while tuple(indices[nbegins]) == begins:
      nbegins += 1
    indices = indices[nbegins:]

    # Unpad the end
    if tuple(indices[-1]) == ends:
      indices = indices[:-1]

    return indices

  #=====================================================================
  # Unpad a vector of vectors
  def unpad_vecs(self, vectors):
    """"""

    if self.reverse:
      begins = np.concatenate([lib.stop_vec()  for lib in self.libs])
      ends   = np.concatenate([lib.start_vec() for lib in self.libs])
    else:
      begins = np.concatenate([lib.start_vec() for lib in self.libs])
      ends   = np.concatenate([lib.stop_vec()  for lib in self.libs])

    # Unpad the beginning
    nbegins = 0
    while tuple(vectors[nbegins]) == begins:
      nbegins += 1
    vectors = vectors[nbegins:]

    # Unpad the end
    if tuple(vectors[-1]) == ends:
      vectors = vectors[:-1]

    return vectors
    
  #=====================================================================
  # Converts a dataset into the expected format (in place)
  def convert_dataset(self, dataset):
    """"""
    
    for i, datum in enumerate(dataset):
      if self.reverse:
        string = datum[1][::-1]
      else:
        string = datum[1][:]
      dataset[i] = (datum[0], self.strs_to_idxs(self.pad_strs(string)))
      
  #=====================================================================
  # Converts a vector to a set of indices
  def vec_to_idxs(self, vector):
    """"""
    
    if self.reverse:
      vectors = self.vec_to_vecs(vector)[::-1]
    else:
      vectors = self.vec_to_vecs(vector)
    loc = 0
    indices = []
    for lib in self.libs:
      indices.append(lib.vecs_to_idxs(vectors[:,loc:loc+lib.wsize()]))
      loc += lib.wsize()
    return np.concatenate(indices, axis=1)
  
  #=====================================================================
  # Converts a vector to a set of strings 
  def vec_to_strs(self, vector):
    """"""
    
    if self.reverse:
      vectors = self.vec_to_vecs(vector)[::-1]
    else:
      vectors = self.vec_to_vecs(vector)
    loc = 0
    strings = []
    for lib in self.libs:
      strings.append(lib.vecs_to_strs(vectors[:,loc:loc+lib.wsize()]))
      loc += lib.wsize()
    return zip(*strings)
  
  #=====================================================================
  # Calculate the cost of a minibatch using multiple cores
  def batch_cost(self, dataset, workers=2):
    """"""
    
    cost = 0
    dataQueue = mp.Queue()
    costQueue = mp.Queue()
    processes = []
    for datum in dataset:
      dataQueue.put(datum)
    for worker in xrange(workers):
      dataQueue.put('STOP')
    for worker in xrange(workers):
      process = mp.Process(target=cost_worker, args=(self.vec_to_cost, dataQueue, costQueue))
      process.start()
      processes.append(process)
    for worker in xrange(workers):
      for miniCost in iter(costQueue.get, 'STOP'):
        cost += miniCost
    for process in processes:
      process.join()
    return cost / len(dataset)
  
  #=====================================================================
  # Calculate the gradients of a minibatch using multiple cores
  def train(self, dataset, grader, optimizer, batchSize=64, epochs=1, costEvery=None, testset=None, saveEvery=None, savePipe=None, workers=2):
    """"""
    
    #-------------------------------------------------------------------
    # Saving and printing
    s = ''
    epochd = str(int(np.log10(epochs))+1)
    minibatchd = str(int(np.log10(len(dataset)/batchSize))+1)
    s += ('Minibatch %0'+epochd+'d-%0'+minibatchd+'d') % (0,0)
    cost = []
    cost.append(self.batch_cost(dataset, workers))
    s += ': %.3f train error' % cost[-1]
    if testset is not None:
      test = []
      test.append(self.batch_cost(testset, workers))
      s += ', %.3f test error' % test[-1]
    wps = 0.0
    s += ', %.1f data per second' % wps
    if saveEvery is not None:
      savePipe.send((self, cost) + ((test,) if testset is not None else tuple()))
      lastSaveTime = time.time()
      s += ', %.1f minutes since saving' % ((time.time()-lastSaveTime)/60)
    s += '        \r'
    print s,
    sys.stdout.flush()
    lastCostTime = time.time()

    #-------------------------------------------------------------------
    # Multiprocessing the minibatch
    dataQueue = mp.Queue()
    gradQueue = mp.Queue()
    recentCost = []
    for t in xrange(epochs):
      np.random.shuffle(dataset)
      for mb in xrange(len(dataset)/batchSize):
        processes = []
        for datum in dataset[mb*batchSize:(mb+1)*batchSize]:
          dataQueue.put(datum)
        for worker in xrange(workers):
          dataQueue.put('STOP')
        for worker in xrange(workers):
          process = mp.Process(target=grad_worker, args=(grader, dataQueue, gradQueue))
          process.start()
          processes.append(process)
        for worker in xrange(workers):
          for grads in iter(gradQueue.get, 'STOP'):
            recentCost.append(grads['cost'])
            self.update_grads(batchSize, *grads['grads'])
        for process in processes:
          process.join()
        optimizer()
        self.reset_grad()

        #---------------------------------------------------------------
        # More printing and saving
        if costEvery is not None  and (mb+1) % costEvery == 0:
          cost.append(np.sum(recentCost)/(batchSize*costEvery))
          recentCost = []
          if testset is not None:
            test.append(self.batch_cost(testset, workers))
          thisCostTime = time.time()
        if saveEvery is not None and (mb+1) % saveEvery == 0:
          savePipe.send((self, cost) + ((test,) if testset is not None else tuple()))
          lastSaveTime = time.time()
        if costEvery is not None and (mb+1) % costEvery == 0:
          s = ''
          s += ('Minibatch %0'+epochd+'d-%0'+minibatchd+'d') % (t+1,mb+1)
          s += ': %.3f train error' % cost[-1]
          if testset is not None:
            s += ': %.3f test error' % test[-1]
          if wps == 0:
            wps = ((batchSize*costEvery) / (thisCostTime-lastCostTime))
          else:
            wps = .67*wps + .33*((batchSize*costEvery) / (thisCostTime-lastCostTime))
          s += ', %.1f data per second' % wps
          if saveEvery is not None:
            s += ', %.1f minutes since saving' % ((time.time()-lastSaveTime)/60)
          s += '        \r'
          print s,
          sys.stdout.flush()
          lastCostTime = time.time()

      #-----------------------------------------------------------------
      # If we haven't been printing, print now
      if costEvery is None or (mb+1) % costEvery != 0:
        if costEvery is None:
          cost.append(np.sum(recentCost)/len(dataset))
        else:
          cost.append(np.sum(recentCost)/(len(dataset) % (batchSize*costEvery)))
        recentCost = []
        if testset is not None:
          test.append(self.batch_cost(testset, workers))
        thisCostTime = time.time()
      if saveEvery is not None and (mb+1) % saveEvery != 0:
        savePipe.send((self, cost) + ((test,) if testset is not None else tuple()))
        lastSaveTime = time.time()
      s = ''
      s += ('Minibatch %0'+epochd+'d-%0'+minibatchd+'d') % (t+1,mb+1)
      s += ': %.3f train error' % cost[-1]
      if testset is not None:
        s += ': %.3f test error' % test[-1]
      if wps == 0:
        wps = ((batchSize*(mb+1 % costEvery) if costEvery is not None else len(dataset)) / (thisCostTime-lastCostTime))
      else:
        wps = .67*wps + .33*((batchSize*((mb+1) % costEvery) if costEvery is not None else len(dataset)) / (thisCostTime-lastCostTime))
      s += ', %.1f data per second' % wps 
      if saveEvery is not None:
        s += ', %.1f minutes since saving' % ((time.time()-lastSaveTime)/60)
      s += '        \r'
      print s,
      sys.stdout.flush()
      if costEvery is None or (mb+1) % costEvery != 0:
        lastCostTime = time.time()

    #-------------------------------------------------------------------
    # Wrap everything up
    if savePipe is not None:
      savePipe.send((self, cost) + ((test,) if testset is not None else tuple()))
      savePipe.send('STOP')
    print ''
    return cost
  
#***********************************************************************
# Test the program
if __name__ == '__main__':
  """"""

  import os.path
  WORKERS = 1
  EPOCHS = 10
  ETA_0 = .1
  PATH = 'Tests'
  HFUNC = 'softplus'
  
  glove = pkl.load(open('glove.6B.10k.50d-real.pkl'))
  #=====================================================================
  # Test the Classifier
  dataset = []
  mat = np.concatenate([glove[0], np.random.randn(3,50)])
  mat = (mat-np.mean(mat))/np.std(mat)
  glove[1]['<S>'] = len(glove[1])
  glove[1]['</S>'] = len(glove[1])
  glove[1]['<UNK>'] = len(glove[1])
  lib = Library(keys=glove[1], mat=mat)
  classifier = Classifier([lib], [50, 50], sharelib=False, hfunc=HFUNC)
  for i in xrange(5):
    dataset.extend(list(enumerate(mat+np.random.normal(0,.1,size=mat.shape))))
  for i, datum in enumerate(dataset):
    dataset[i] = (datum[1], [datum[0]])
  np.random.shuffle(dataset)
  train_data = dataset[:int(.8*len(dataset))]
  dev_data = dataset[int(.8*len(dataset)):int(.9*len(dataset))]
  test_data = dataset[int(.9*len(dataset)):]
  train_data = classifier.convert_dataset(train_data)
  dev_data = classifier.convert_dataset(dev_data)
  test_data = classifier.convert_dataset(test_data)
  grads, opt = classifier.RMSProp(eta_0=ETA_0)
  
  parentPipe, childPipe = mp.Pipe()
  process = mp.Process(target=pkl_worker, args=(childPipe,), kwargs={'path':PATH, 'name': 'cls-%s-'%HFUNC})
  process.start()
  classifier.train(train_data, grads, opt, savePipe=parentPipe, costEvery=10, epochs=EPOCHS, testset=dev_data)
  process.join()
  
  glove = pkl.load(open('glove.6B.10k.50d-real.pkl'))
  #=====================================================================
  # Test the Encoder
  dataset = zip([list(x) for x in sorted(glove[1], key=glove[1].get)], glove[0])
  vocab = set()
  for datum in dataset:
    vocab.update(datum[0])
  lib = Library(keys=vocab, mat=50)
  encoder = Encoder([lib], [50,200,50], model='RNN')
  encoder.convert_dataset(dataset)
  train_data = dataset[:int(.8*len(dataset))]
  dev_data = dataset[int(.8*len(dataset)):int(.9*len(dataset))]
  test_data = dataset[int(.9*len(dataset)):]
  grads, opt = encoder.Adam()
  
  parentPipe, childPipe = mp.Pipe()
  process = mp.Process(target=pkl_worker, args=(childPipe,), kwargs={'name': 'enc-'})
  process.start()
  encoder.train(train_data, grads, opt, savePipe=parentPipe, costEvery=10, epochs=EPOCHS, testset=dev_data)
  process.join()
  
  #=====================================================================
  # Test the Decoder
  #dataset = zip(glove[0], [list(x) for x in sorted(glove[1], key=glove[1].get)])
  #vocab = set()
  #decoder = Decoder([lib], [100,200,50], model='RNN')
  #decoder.convert_dataset(dataset)
  #train_data = dataset[:int(.8*len(dataset))]
  #dev_data = dataset[int(.8*len(dataset)):int(.9*len(dataset))]
  #test_data = dataset[int(.9*len(dataset)):]
  #grads, opt = decoder.SGD()
  #
  #parentPipe, childPipe = mp.Pipe()
  #process = mp.Process(target=pkl_worker, args=(childPipe,), kwargs={'name': 'dec-'})
  #process.start()
  #decoder.train(train_data, grads, opt, savePipe=parentPipe, costEvery=10, workers=WORKERS, epochs=EPOCHS, testset=dev_data)
  #process.join()
  #for hparam in decoder.hparams:
  #  print hparam.get_value()
  print 'Works!'
