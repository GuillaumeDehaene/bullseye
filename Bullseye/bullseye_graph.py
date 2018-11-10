"""
    The ``bullseye_graph`` module
    ======================
 
    Contains the definition of the Bullseye.Graph class, allowing to create a graph 
    with specific options and to launch the Bullseye algorithm.
    
    :Example:
    >>> from Bullseye import Bullseye_graph
    >>> bull = Bullseye.Graph()
    >>> bull.feed_with(file = "dataset.csv", chunksize = 50, k=10)
    >>> bull.set_model("multilogit")
    >>> bull.init_with(mu_0 = 0, cov_0 = 1)
    >>> bull.set_options(m = 0, compute_kernel = True)
    >>> bull.build()
    >>> bull.run()
"""
import tensorflow as tf
import numpy as np
import pandas as pd
import os
import time

from .graph import construct_bullseye_graph
from .predefined_functions import get_predefined_functions
from .utils import *
from .warning_handler import *
from .profilers import TimeLiner, Profiler

"""
    The ``Bullseye`` class
    ======================
"""

class Graph:
    def __init__(self):
        """
        initialization of Bullseye_graph.
        does not take any parameters.
        """
        
        #listing of all fundamental attributes
        attrs = [
            #the graph itself
            "graph",
            #data related
            "d","k","p","X","Y","prior_std","file","chunksize","nb_of_chunks",
            #init
            "mu_0","cov_0",
            #functions
            "Phi","grad_Phi","hess_Phi","Projs"
            ]
            
        #listing of all option attributes and their default values
        options = {
                #if brutal iteration, the ELBO will be updated even if it decreases
                "brutal_iteration"          : False,
                #default speed of the algorithm, γ will start with this value
                "speed"                     : 1,
                #γ will decrease as γ*step_size_decrease_coef when ELBO
                # did not increase enough
                "step_size_decrease_coef"   : 0.5,
                #number of sample to approximate expectations
                "s"                         : 10,
                #quadrature_deg is the degree of the gaussian hermite
                # quadrature used to approximated prior expectation
                "quadrature_deg"            : 12,
                #we have s observations of the activations. make flatten
                # activations to True in order to flatten the observations
                # into a large unique observation.
                "flatten_activations"       : False,
                #when computing the local variances, compute the square roots
                # one by one or use a trick to compute only the square root 
                # of cov
                "local_std_trick"           : True ,
                #number of batches for the likelihood
                "m"                         : 0,
                #number of batches for the prior
                "m_prior"                   : 0,
                #when computing ABA^T, compute the kernels H=AA^T for fast
                #computations of ABA^T, but exponential space is required
                "compute_kernel"            : False,
                #same as compute_kernel, but for the prior
                "compute_prior_kernel"      : False,
                #/!\ NOT STABLE /!\
                #tests to improve capacities with sparse matrices
                "sparse"                    : True,
                #when streaming a file, if chunk_as_sum is true, does not
                # keep track of the different values of eᵢ,ρᵢ,βᵢ in order
                # to save space
                "chunk_as_sum"              : True,
                #when prior covariance is diagonal, prevents the use of 
                # exponential space and improves speed
                "keep_1d_prior"             : True,
                #when streaming a file, consider only a given number of
                # chunks
                "number_of_chunk_max"       : 0,
                #use the natural value of eᵢ,ρᵢ,βᵢ centered in 0
                "natural_param_likelihood"  : False,
                #same as natural_param_likelihood for the prior
                "natural_param_prior"       : False,
                #make the run silent or not
                "silent"                    : True
                }        
        self.option_list = list(options)
        self.in_graph = None
        
        for key in attrs:
            setattr(self, key, None)
        for key in self.option_list:
            setattr(self, key, options[key])
            
        #attributes that ensure the correct construction of the graph
        self.feed_with_is_called = False
        self.set_model_is_called = False
        self.init_with_is_called = False
        self.build_is_called = False
        
    def feed_with(self, X = None, Y = None, d = None, k = None, prior_std = 1,
        file = None, chunksize = None, **kwargs):
        """
        Feed the graph with data. There are multiple ways of doing so.
        In all the cases, it requires prior_std.
        
        Method 1: requires X and Y
            feed with a design matrix X and a response matrix Y
        Method 2: requires d and k
            in the case X and Y are not defined yet, you only
             need to specify there sizes with d and k. in this
             case, X and Y need to be specified when run() will
             be called
        Method 3: requires file, chunksize and k
            to stream a file.
        
        :param X: design matrix.
        :type X: np.array [None,d]
        :param Y: response matrix.
        :type Y: np.array [None, k]
        :param d: d
        :type d: int 
        :param k: k
        :type k: int
        :param prior_std: prior std matrix. can also be a vector or a int if it is
            diagonal.
        :type prior_std: int, np.array[p] or np.array [p,p]
        :param file: path of the file to stream (.csv format)
        :type file: string
        :param chunksize: chunksize
        :type int:
        """
    
        #method 1
        if X is not None or Y is not None:
            assert X is not None
            assert Y is not None
            assert Y.shape[0] == X.shape[0]
            assert len(X.shape) in [1,2]
            assert len(Y.shape) in [1,2]
            
            if len(X.shape)==1:
                X = expand_dims(X,0)
            if len(Y.shape)==1:
                Y = expand_dims(Y,0)
            
            self.X = X
            self.Y = Y
            self.d = X.shape[-1]
            self.k = Y.shape[-1]
        #method 2
        elif file is None:
            assert d is not None
            assert k is not None
            assert type(d), type(k) == [int,int]            
            
            self.d = d
            self.k = k
        #method 3
        else:
            #TODO to see again
            assert os.path.isfile(file)
            if chunksize is not None:
                assert type(chunksize)==int
            assert k is not None
            assert type(k) == int
            
            #read one line of the file to deduce d
            reader = pd.read_table(file, sep=",", chunksize = 1)
            for chunk in reader:
                data = list(chunk.shape)
                break
            
            n = sum(1 for line in open(file))
            
            self.k = k
            self.d = data[-1]-1
            self.file = file
            self.chunksize = chunksize  
            #TODO to see again
            self.num_of_chunks = int(n/chunksize)
        #compute p once for all
        self.p = self.d * self.k
        
        #handle std_prior
        # depending on the form of the given std_prior, transform it into a p×p matrix.
        if type(prior_std) == int:
            self.prior_std = prior_std * np.eye(self.p)
        elif len(list(prior_std.shape)) == 1:
            assert list(prior_std.shape)==[self.p]
            self.prior_std = np.diag(prior_std)
        else:
            assert list(prior_std.shape) == [self.p, self.p]
            self.prior_std = prior_std
        
        #remember this method is called, to prevent errors
        self.feed_with_is_called = True
            
    def set_model(self, model = None, phi_option="", proj_option="",
        Phi = None, grad_Phi = None, hess_Phi = None, Projs = None):
        """
        Specify to the graph a given model.
        There are multiple ways of doing so.
        
        Method 1: requires model
            make use of the ``predefined_functions`` module. in particular, model, 
             phi_option and proj_option can be specified in order to obtain the desired 
             form
        Method 2: requires Phi, grad_Phi and hess_Phi
            manually choose the function φ. you also need to compute ∇φ and Hφ.
             the functions take as parameter an activation matrix and Y.
        
        :param model: the model, e.g. "multilogit"
        :type model: string
        :param phi_option: phi_option for ``predefined_functions``, e.g. "mapfn"
        :type Y: string
        :param proj_option: proj_option for ``predefined_functions``, e.g. "mapfn"
        :type d: string
        :param Phi: φ
        :type Phi: (A,Y)->φ(A,Y)
        :param grad_Phi: ∇φ
        :type grad_Phi: (A,Y)->∇φ(A,Y)
        :param hess_Phi: Hφ
        :type hess_Phi: (A,Y)->Hφ(A,Y)
        """
        
        #method 1
        if model is not None:
            self.Phi, self.grad_Phi, self.hess_Phi, self.Projs =\
                get_predefined_functions(model,
                                        phi_option = phi_option,
                                        proj_option = proj_option)
        #method 2
        else:
            assert Phi is not None
            self.Phi = Phi
            self.grad_Phi = grad_Phi
            self.hess_Phi = hess_Phi
            self.Projs = Projs
            
        #remember this method is called, to prevent errors    
        self.set_model_is_called = True
    
    def init_with(self, mu_0 = 0, cov_0 = 1):
        """
        Specify μ₀ and Σ₀ from which the Bullseye algorithm should start.
        
        :param mu_0: μ₀
        :type mu_0: float, or np.array [p]
        :param cov_0: Σ₀
        :type cov_0: float, np.array [p] or [p,p]
        """
        #handle μ₀
        if type(mu_0) in [float, int]:
            self.mu_0 = mu_0 * np.ones(self.p)
        elif len(mu_0.shape) == 1:
            assert list(mu_0.shape) == [p]
            self.mu_0 = mu_0
        
        #handle Σ₀
        if type(cov_0) in [float, int]:
            self.cov_0 = cov_0 * np.eye(self.p)
        elif len(cov_0.shape) == 1:
            assert list(mu_0.shape) == [self.p]
            self.cov_0 = np.diag(cov_0)
        else:
            assert list(mu_0.shape) == [self.p,self.p]
            self.cov_0 = cov_0
            
        #remember this method is called, to prevent errors
        self.init_with_is_called = True
            
    def set_options(self, **kwargs):
        """
        Specify μ₀ and Σ₀ from which the Bullseye algorithm should start.
        
        :param mu_0: μ₀
        :type mu_0: float, or np.array [p]
        :param cov_0: Σ₀
        :type cov_0: float, np.array [p] or [p,p]
        """
        for key in list(kwargs):
            if key not in self.option_list:
                warn_unknown_parameter(key, function = "Bullseye_graph.set_options()")
            else:
                setattr(self, key, kwargs[key])
                
        #dependent options
        if self.keep_1d_prior:
            if "compute_prior_kernel" in list(kwargs):
                warn_useless_parameter("computed_prior_kernel", "keep_1d_prior",
                    function = "Bullseye_graph.set_options()")
            self.compute_prior_kernel = False
    
    def build(self):
        """
        builds the implicit tensorflow graph.
        """
        #to prevent error, ensures feed_with(), set_model() and init_with() are already
        # called
        assert self.feed_with_is_called and self.set_model_is_called\
            and self.init_with_is_called
        
        self.graph, self.in_graph = construct_bullseye_graph(self)
        
        #remember this method is called, to prevent errors
        self.build_is_called = True
    
    def run(self, n_iter = 10, X = None, Y = None,
            keep_track=False, timeline_path=None, profiler_dir=None):
        """
        run the implicit tensorflow graph.
        
        :param n_iter: the number of iterations of the Bullseye algorithm
        :type n_iter: int
        :param X: allow the end user to specify a X if it hasn't been yet
        :type X: np.array [None, d]
        :param Y: allow the end user to specify a X if it hasn't been yet
        :type Y: np.array [None, k]
        :param keep_track: keep track of all mus,covs,elbos and so on
        :type keep_track: boolean
        
        :return: μ's, Σ's, ELBOs, and times of each iteration
        :rtype: dict
        """
        #to prevent error, ensures the graph is already built
        assert self.build_is_called
        
        #initialize lists  to return
        mus = []
        covs = []
        elbos = []
        times = []
        status = []
        
        #easy access to the tensorflow graph operations
        ops = self.in_graph
        
        #for timing
        run_options = None
        run_metadata = None
        runs_timeline = None
        run_kwargs = {}
        if timeline_path is not None:
            run_options = tf.RunOptions(trace_level=tf.RunOptions.FULL_TRACE)
            run_metadata = tf.RunMetadata()
            runs_timeline = TimeLiner()
            run_kwargs = {'options' : run_options, 'run_metadata' : run_metadata}
            
        #for profiler
        profiler = None
        if profiler_dir is not None:
            profiler = Profiler(profiler_dir)
        
        #start the session
        with tf.Session(graph = self.graph) as sess:
            #initialize the graph
            sess.run(ops["init"],**run_kwargs)
            
            #starting iterations
            for epoch in range(n_iter):
                start_time = time.time()
                
                #feeding dict
                d_computed = {}
                
                #compute new e, rho, beta
                if self.file is None:
                    #using given X and Y
                    if X is not None and Y is not None:
                        d_computed['X:0'] = X
                        d_computed['Y:0'] = Y
                    # or using X,Y already given to the graph
                    else:
                        if X is None:
                            assert self.X is not None
                        if Y is None:
                            assert self.Y is not None
                    #note that e,rho and beta will in this case be computed at the same
                    # time as the ELBO
                else:
                    #read chunks, and update global e, rho and beta in consequence
                    self.set_globals_from_chunks(self.file,
                                                sess,
                                                run_kwargs = run_kwargs,
                                                runs_timeline = runs_timeline,
                                                profiler = profiler)
                #prepare profiler
                if profiler is not None:
                    profiler.prepare_next_step()
                    
                #compute new elbo
                statu, elbo = sess.run(ops["iteration"], feed_dict = d_computed)
                
                #handle timeline
                if runs_timeline is not None:
                    runs_timeline.update_timeline(run_metadata)
                #handle profiler
                if profiler is not None:
                    profiler.profile_operations()
                
                #save the current state
                if keep_track:
                    mu, cov = sess.run([ops["mu"], ops["cov"]])
                    mus.append(mu)
                    covs.append(cov)
                    
                print('{statu}, with {elbo}'.format(statu = statu, elbo = elbo))
                
                status.append(statu)
                times.append(time.time()-start_time)
            
            #get the lasts mu, covs
            if not keep_track:
                mus, covs = sess.run([ops["mu"], ops["cov"]])
                                                                                 
            #save the run timeline
            if timeline_path is not None:
                runs_timeline.save(timeline_path)
        
        #end of session and return
        return {"mus" : mus,
                "covs" : covs,
                "elbos" : elbos,
                "times" : times,
                "status" : status}
            
    def set_globals_from_chunks(self, file, sess, run_kwargs, runs_timeline, profiler):
        """
        update global_e, global_rho and global_beta while streaming through the file
        
        :param file: file to stream
        :type file: string
        :param sess: the current tensorflow session
        :type sess: tf.Session()
        """
        #create a pandas reader
        reader = pd.read_table(self.file, 
                               sep = ",",
                               chunksize = self.chunksize)
        #for simplicity
        ops = self.in_graph
        
        if self.chunk_as_sum:
            sess.run(ops["init_globals"],**run_kwargs)
        
        #start streaming
        for (i,chunk) in enumerate(reader):
            #read data
            data = np.asarray(chunk)
            #transform them TODO to see again
            X = data[:,1:]/253.
            Y = to_one_hot(data[:,0])
            #create the feeding dict
            d_ = {"X:0" : X, "Y:0" : Y}
            
            #prepare profiler
            if profiler is not None:
                profiler.prepare_next_step()
            
            #add to e,ρ and β current eᵢ,ρᵢ and βᵢ
            if self.chunk_as_sum:
                sess.run(ops["update_globals"], feed_dict = d_, **run_kwargs)
            #chunk as list : append current eᵢ, ρᵢ and βᵢ to [eᵢ],[ρᵢ],[βᵢ]
            else:
                sess.run(ops["update_globals"][i], feed_dict = d_, **run_kwargs)
            
            #handle timeline
            if runs_timeline is not None:
                runs_timeline.update_timeline(run_kwargs["run_metadata"])
            #handle profiler
            if profiler is not None:
                profiler.profile_operations()
            
            #end of one chunk
            if not self.silent:
                print("one chunk done")
            
            #if self.number_of_chunk_max had been set, see if we can continue
            if self.number_of_chunk_max != 0 and i>=self.number_of_chunk_max:
                break