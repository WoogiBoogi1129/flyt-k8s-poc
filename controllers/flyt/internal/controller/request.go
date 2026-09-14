package controller

import (
    "context"
    "errors"
    "fmt"

    api "github.com/WoogiBoogi1129/flyt-k8s-poc/controllers/flyt/api/v1alpha1"
    apierrors "k8s.io/apimachinery/pkg/api/errors"
    "k8s.io/apimachinery/pkg/api/meta"
    metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
    "k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
    "k8s.io/apimachinery/pkg/types"
    ctrl "sigs.k8s.io/controller-runtime"
    "sigs.k8s.io/controller-runtime/pkg/client"
)

// Requests validate intent and report application. They never create Workers;
// VMI reconciliation is the sole creator, preventing competing allocation loops.
type RequestReconciler struct{Base}

func workerBound(w *api.FlytWorker)bool{
    if w==nil||!w.DeletionTimestamp.IsZero()||w.Spec.Suspend||w.Status.Phase!="Ready"||w.Status.ObservedGeneration!=w.Generation{return false}
    c:=meta.FindStatusCondition(w.Status.Conditions,"Ready")
    return c!=nil&&c.Status==metav1.ConditionTrue&&c.ObservedGeneration==w.Generation
}
func (r *RequestReconciler) report(ctx context.Context,q *api.FlytGPURequest,resources *api.GPUResources,w *api.FlytWorker,consumers int32,accepted bool,phase,reason,msg string)(ctrl.Result,error){
    err:=r.status(ctx,q,phase,reason,msg,func(s *api.Status){
        s.Consumers=consumers
        q.Status.Requested=resources;q.Status.Applied=nil;q.Status.AppliedRequestGeneration=0
        q.Status.WorkerRef=nil;q.Status.VMIRef=nil
        s.Endpoint="";s.VMIP="";s.PodIP="";s.PodUID="";s.WorkerGeneration="";s.ManagerEpoch=""
        a:=metav1.ConditionFalse;if accepted{a=metav1.ConditionTrue}
        meta.SetStatusCondition(&s.Conditions,metav1.Condition{Type:"Accepted",Status:a,ObservedGeneration:q.Generation,Reason:reason,Message:msg})
        applied:=metav1.ConditionFalse;appliedReason:="NotBound";appliedMessage:="No current Ready Worker observed"
        if w!=nil{
            ref:=identity(w);q.Status.WorkerRef=&ref;vref:=w.Spec.VMIRef;q.Status.VMIRef=&vref
            if workerBound(w)&&w.Spec.Request!=nil{
                value:=w.Spec.Request.Resources;q.Status.Applied=&value;q.Status.AppliedRequestGeneration=w.Spec.Request.Generation
                s.Endpoint=w.Status.Endpoint;s.VMIP=w.Status.VMIP;s.PodIP=w.Status.PodIP;s.PodUID=w.Status.PodUID
                s.WorkerGeneration=w.Status.WorkerGeneration;s.ManagerEpoch=w.Status.ManagerEpoch
                applied=metav1.ConditionTrue;appliedReason="WorkerBound";appliedMessage="Frozen allocation has a Ready Worker; this is not a GPU execution test"
            }
        }
        meta.SetStatusCondition(&s.Conditions,metav1.Condition{Type:"Applied",Status:applied,ObservedGeneration:q.Generation,Reason:appliedReason,Message:appliedMessage})
    })
    return ctrl.Result{RequeueAfter:Period},err
}
func (r *RequestReconciler) Reconcile(ctx context.Context,req ctrl.Request)(ctrl.Result,error){
    q:=&api.FlytGPURequest{};if err:=r.Reader.Get(ctx,req.NamespacedName,q);err!=nil{return ctrl.Result{},client.IgnoreNotFound(err)}
    workers,err:=r.workers(ctx,q.Namespace);if err!=nil{return ctrl.Result{},err}
    matches:=[]api.FlytWorker{}
    for _,w:=range workers{if w.Spec.Request!=nil&&w.Spec.Request.RequestRef==identity(q){matches=append(matches,w)}}
    count:=int32(len(matches));var w *api.FlytWorker;if len(matches)==1{w=&matches[0]}
    if !q.DeletionTimestamp.IsZero(){
        for i:=range matches{if err=r.deleteWorker(ctx,&matches[i]);err!=nil{return ctrl.Result{},err}}
        if count>0{return r.report(ctx,q,nil,w,count,false,"Terminating","WorkersReleasing","Waiting for referenced Workers to finish their binding and Pod cleanup")}
        _,err=r.finalizer(ctx,q,false);return ctrl.Result{},err
    }
    if changed,err:=r.finalizer(ctx,q,true);changed||err!=nil{return ctrl.Result{Requeue:true},err}
    vm,err:=r.requestVM(ctx,q)
    if err!=nil{
        if apierrors.IsNotFound(err)||errors.Is(err,errVMIdentity){
            for i:=range matches{if e:=r.deleteWorker(ctx,&matches[i]);e!=nil{return ctrl.Result{},e}}
        }
        return r.report(ctx,q,nil,w,count,false,"Blocked","VMUnavailable",err.Error())
    }
    if len(q.OwnerReferences)==0{
        old:=q.DeepCopy();q.OwnerReferences=[]metav1.OwnerReference{{APIVersion:"kubevirt.io/v1",Kind:"VirtualMachine",Name:vm.GetName(),UID:vm.GetUID(),Controller:boolp(false),BlockOwnerDeletion:boolp(false)}}
        return ctrl.Result{Requeue:true},r.Patch(ctx,q,client.MergeFromWithOptions(old,client.MergeFromWithOptimisticLock{}))
    }
    if len(q.OwnerReferences)!=1||q.OwnerReferences[0].UID!=vm.GetUID(){return r.report(ctx,q,nil,w,count,false,"Blocked","OwnershipConflict","Request owner must be its referenced VM UID")}
    resources,err:=normalizeRequest(q.Spec)
    if err!=nil{return r.report(ctx,q,nil,w,count,false,"Blocked","InvalidResources",err.Error())}
    p,cp,err:=r.requestDependencies(ctx,q)
    if err!=nil{return r.report(ctx,q,&resources,w,count,false,"Blocked","DependencyUnavailable",err.Error())}
    if err=withinProfile(resources,p);err!=nil{return r.report(ctx,q,&resources,w,count,false,"Blocked","ProfileLimit",err.Error())}
    if !p.DeletionTimestamp.IsZero()||!cp.DeletionTimestamp.IsZero(){return r.report(ctx,q,&resources,w,count,false,"Blocked","DependencyDeleting","No new allocations while dependencies are deleting; existing Workers retain cleanup protection")}
    if count>1{return r.report(ctx,q,&resources,nil,count,false,"Blocked","MultipleWorkers","Waiting for a single VMI allocation; no new Worker should be admitted")}
    v:=vmi();err=r.Reader.Get(ctx,types.NamespacedName{Namespace:q.Namespace,Name:q.Spec.VMRef.Name},v)
    if apierrors.IsNotFound(err){
        annotations,_,_:=unstructured.NestedStringMap(vm.Object,"spec","template","metadata","annotations")
        if annotations[RequestAnnotation]!=q.Name{return r.report(ctx,q,&resources,w,count,true,"Pending","NotSelected","Select this request in VM spec.template.metadata.annotations")}
        if annotations["flyt.dev/profile"]!=""||annotations["flyt.dev/control-plane"]!=""{return r.report(ctx,q,&resources,w,count,false,"Blocked","InputConflict","Request selection cannot be combined with legacy profile/control-plane annotations")}
        return r.report(ctx,q,&resources,w,count,true,"Pending","WaitingForVMI","Request accepted within profile limits; waiting for a Running VMI")
    }
    if err!=nil{return ctrl.Result{},err}
    if !matchesVM(v,q.Spec.VMRef){return r.report(ctx,q,&resources,w,count,false,"Blocked","VMIdentityMismatch","Current VMI does not belong to the referenced VM UID")}
    if terminal(v){return r.report(ctx,q,&resources,w,count,true,"Pending","WaitingForVMI","Current VMI is terminating; waiting for its replacement")}
    snapshot,err:=readSnapshot(v);if err!=nil{return r.report(ctx,q,&resources,w,count,false,"Blocked","InvalidSnapshot",err.Error())}
    if snapshot==nil&&v.GetAnnotations()[LegacyVMIAnnotation]==string(v.GetUID()){
        return r.report(ctx,q,&resources,w,count,true,"Pending","PendingRestart","This VMI uses legacy allocation; select request mode in the VM template and create a new VMI")
    }
    if snapshot!=nil&&snapshot.Allocation.RequestRef!=identity(q){return r.report(ctx,q,&resources,w,count,true,"Pending","PendingRestart","Current VMI has another frozen request; change VM template selection and restart the VM")}
    if v.GetAnnotations()[RequestAnnotation]!=q.Name{return r.report(ctx,q,&resources,w,count,true,"Pending","PendingRestart","Current VMI does not select this request; select it in the VM template for the next VMI")}
    if v.GetAnnotations()["flyt.dev/profile"]!=""||v.GetAnnotations()["flyt.dev/control-plane"]!=""{return r.report(ctx,q,&resources,w,count,false,"Blocked","InputConflict","Request selection cannot be combined with legacy profile/control-plane annotations")}
    if snapshot!=nil&&snapshot.Allocation.Resources!=resources{return r.report(ctx,q,&resources,w,count,true,"Pending","PendingRestart","Updated request is accepted; frozen allocation remains until a new VMI is created")}
    if w==nil{
        for _,other:=range workers{if other.Spec.VMIRef.UID==string(v.GetUID()){
            return r.report(ctx,q,&resources,nil,count,false,"Blocked","InputConflict","Current VMI already has a Worker from another input mode; restart is required")
        }}
        return r.report(ctx,q,&resources,nil,count,true,"Pending","WaitingForWorker","Request accepted; VMI reconciler will freeze and create its Worker")
    }
    if w.Spec.Request.Resources!=resources{return r.report(ctx,q,&resources,w,count,true,"Pending","PendingRestart","Current Worker uses its frozen request; changes require a new VMI")}
    if workerBound(w){return r.report(ctx,q,&resources,w,count,true,"Ready","Bound","Requested resources match the current Ready Worker allocation; GPU execution remains unvalidated")}
    phase:="Pending";if w.Spec.Suspend{phase="Suspended"}
    return r.report(ctx,q,&resources,w,count,true,phase,"WorkerNotReady",fmt.Sprintf("Worker phase=%s; inspect its Ready condition for allocation details",w.Status.Phase))
}
